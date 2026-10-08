# Den Dienst installieren und starten

Es gibt vier Wege, den Dienst zum Laufen zu bringen. Der erste ist für eine Person, die ihn in einer Minute starten will und nur Docker braucht. Die letzten beiden sind für die Arbeit am Code. Alle erzeugen denselben Dienst, und der Rest dieser Seite (Konfiguration, wo der Zustand liegt, Aktualisieren, was bei einem Fehlstart zu prüfen ist) gilt für jeden von ihnen.

| Weg | Sie brauchen | Sie erhalten |
| --- | --- | --- |
| [A. Docker, aus den veröffentlichten Images](#weg-a-docker-aus-den-veröffentlichten-images) | Docker | Das Dashboard auf `:8080`, den Zustand auf einem Volume, Agenten als Subprozesse. Nichts zu klonen oder zu bauen. |
| [B. Docker Compose aus einem Checkout](#weg-b-docker-compose-aus-einem-checkout) | Docker, git | Dasselbe, aus dem Quellcode gebaut, dazu Container pro Agent, den Browser-Dienst und die Profile `postgres`, `scale` und `ha` |
| [C. Das Installationsprogramm](#weg-c-das-installationsprogramm) | Python 3.11+, Node 22+ | Eine virtuelle Umgebung, den Befehl `ah` in Ihrem PATH, das Dashboard mit Hot Reload |
| [D. Von Hand](#weg-d-von-hand) | Python 3.11+, Node 22+ | Weg C in seine Einzelteile zerlegt, für eine andere Form |

Mehr als ein Backend zu betreiben, oder Backends und Worker auf verschiedenen Hosts, ist eher ein Deployment als eine Installation: [deployment](deployment.md) listet die Formen auf.

Jeder der Wege A bis C lässt sich auch einrichten, indem Sie Fragen beantworten: siehe die [geführte Einrichtung](#geführte-einrichtung-ah-setup) weiter unten.

## Voraussetzungen

| Werkzeug | Version | Nötig für |
| --- | --- | --- |
| Docker + Compose | jede aktuelle | Wege A und B sowie die Agentenausführung in Docker bei C und D |
| Python | 3.11+ | Wege C und D: Backend, CLI, Agent-Runner |
| Node.js + npm | 22+ | Wege C und D: das Dashboard |
| Ein Anbieterschlüssel | | OpenAI, Anthropic, Google oder ein lokales Ollama / LM Studio. Lässt sich nach dem Start in den Einstellungen hinzufügen |

## Weg A: Docker, aus den veröffentlichten Images

Jedes Release veröffentlicht das Backend und das Dashboard als Images auf GHCR (`ghcr.io/snooker155/agents-hub-backend`, `ghcr.io/snooker155/agents-hub-frontend`). Das Verzeichnis `deploy/quickstart/` im Repository enthält eine Compose-Datei, die beide für eine Person startet, ohne dass Sie etwas klonen oder bauen müssen:

```bash
mkdir agents-hub && cd agents-hub
curl -fsSLO https://raw.githubusercontent.com/snooker155/agents_hub/main/deploy/quickstart/docker-compose.yml
curl -fsSL  https://raw.githubusercontent.com/snooker155/agents_hub/main/deploy/quickstart/env.example -o .env
docker compose up -d
```

Öffnen Sie `http://localhost:8080`, tragen Sie in den Einstellungen einen Anbieterschlüssel ein (oder entkommentieren Sie ihn vor dem Start in der `.env`), wählen Sie im Chat einen Agenten und senden Sie etwas.

Was läuft: das Backend, in dem jeder Agent als Subprozess läuft (`AGENT_EXECUTION_MODE=local`), und das Dashboard hinter nginx, der auch `/api` weiterleitet. Der Zustand liegt im benannten Volume `agents_hub_data`: `docker compose down` behält ihn, `docker compose down -v` löscht ihn. Die `.env` ist als die Datei, in die die Einstellungen schreiben, in das Backend eingebunden, sodass Schlüssel, die Sie auf dieser Seite eingeben, den Container überdauern. `WEB_PORT` in der `.env` verschiebt das Dashboard. `AGENTS_HUB_TAG` legt ein Release fest, statt `latest` zu folgen (`0.8` für den neuesten Patch dieser Minor-Version, `0.8.0` für genau diese, `0.8.0-rag` für die Variante mit dem RAG-Stack, siehe [deployment](deployment.md), "Releases"). `DEMO_WORKSPACE=1` legt beim ersten Start den [Demo-Workspace](demo.md) an.

```bash
docker compose pull && docker compose up -d                     # aktualisieren
docker compose exec backend python -m cli db backup --to /data/backups
docker compose logs -f backend
```

Der Terminal-Client spricht mit dieser Installation über REST: Aus einem Checkout installiert `pip install -e .` den Befehl `ah` mit nur seinen drei Abhängigkeiten, danach setzen Sie `export AGENTS_HUB_URL=http://localhost:8080` ([cli](cli.md), "Two ways it reaches the service"). Alternativ nutzen Sie die API mit `docker compose exec backend python -m cli ...` im Container.

Was diese Form weglässt: Der Docker-Socket ist nicht eingebunden, daher können Agenten keine eigenen Container bekommen ([containers](containers.md)). Der Sandbox-Anbieter `docker` und der Browser-Dienst brauchen Weg B. Das Image ohne das Suffix `-rag` enthält keinen Embedding-Stack, deshalb bleibt `RAG_VECTOR_DB` auf `none`, solange Sie nicht das Tag mit `-rag` festlegen.

## Weg B: Docker Compose aus einem Checkout

```bash
git clone https://github.com/snooker155/agents_hub.git
cd agents_hub
docker compose up --build              # Backend :8000, Dashboard :8080
```

Die `.env` ist hier optional: Der Stack startet ohne Anbieterschlüssel, und Sie fügen sie in den Einstellungen hinzu. `BACKEND_PORT` und `WEB_PORT` verschieben die veröffentlichten Ports, und das Dashboard folgt ihnen, weil es mit seinem eigenen Origin spricht. Der Checkout ist in das Backend eingebunden, daher landet der Zustand wie bei einer lokalen Installation in `.agents_hub/` darunter. Aktualisieren heißt `git pull`, gefolgt von `docker compose up -d --build`. Der Docker-Socket ist eingebunden, daher können Agenten in eigenen Containern laufen (`AGENT_EXECUTION_MODE=docker`, [containers](containers.md)). `WITH_RAG=true` in der `.env` baut das Image mit dem RAG-Stack.

Das Dashboard ist das gebaute Bundle hinter nginx, der auch `/api` weiterleitet und auf die dahinterliegenden Backends verteilt. Mehr Backends sind also nur ein Flag:

```bash
docker compose --profile scale up --build --scale backend=3
```

Dieselbe Datei bringt die optionalen Dienste als Profile mit: `postgres` (die Datenbank statt SQLite, [scaling](scaling.md)), `scale` (Redis, für mehr als ein Backend-Replikat), `browser` (headless Chromium für die Browser-Werkzeuge) und `ha` (Postgres, Redis, MinIO, Replikate von `api` und `worker`, die Hochverfügbarkeitsform in [deployment](deployment.md)).

Compose hat keinen Vite-Dienst: Für eine HMR-Schleife beim Bearbeiten von Frontend-Code starten Sie `npm run dev` auf dem Host wie bei Weg C. Das Image bauen Sie neu (`docker compose up --build frontend`), wenn die Änderung im Container ankommen soll.

## Weg C: Das Installationsprogramm

```bash
git clone https://github.com/snooker155/agents_hub.git
cd agents_hub
./install.sh
```

Es legt `.venv` an, installiert den Dienst und den Befehl `ah` editierbar, kopiert `.env.example` nach `.env`, falls Sie noch keine `.env` haben, installiert die npm-Pakete des Dashboards und schreibt die Shell-Integration in Ihre Startdatei. Sie können es gefahrlos erneut ausführen: Eine vorhandene `.env` wird nie überschrieben, und der Shell-Block wird an Ort und Stelle neu geschrieben, statt noch einmal angehängt zu werden.

Flags: `--no-frontend` (der Dienst ohne die npm-Pakete des Dashboards), `--cli-only` (nur der Client, zur Verwendung mit `AGENTS_HUB_URL`, und ebenfalls ohne Dashboard), `--with-rag` (fügt die RAG-Extras hinzu, die torch mitbringen), `--with-demo` (schaltet `DEMO_WORKSPACE` in der `.env` ein, sodass der Demo-Workspace beim ersten Start des Dienstes angelegt wird, siehe [demo](demo.md)), `--setup` / `--no-setup` (die [geführte Einrichtung](#geführte-einrichtung-ah-setup), die sonst bei einer Erstinstallation aus einem Terminal läuft und dann die `.env` selbst schreibt), `--no-venv`, `--no-shell`, `--venv PATH`, `--python PATH`.

Danach, in einem neuen Terminal:

```bash
ah up                       # API auf :8000, Dashboard auf :5173
```

## Weg D: Von Hand

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[backend,agents]"     # Dienst + der Befehl ah
cp .env.example .env
cd dashboard/frontend && npm install && cd ../..
```

`pip install -e .` allein installiert nur den Terminal-Client und seine drei Abhängigkeiten. Die Extras werden aus den Requirement-Dateien im Repository gelesen, sodass `[backend]`, `[agents]` und `[rag]` mit ihnen im Gleichschritt bleiben.

`[connectors]` fügt die Treiber hinzu, die die Konnektoren brauchen (MySQL, ClickHouse, msal, google-auth; `requirements-connectors.txt`).

`requirements.lock` legt die genaue Auflösung dieser Requirement-Dateien fest (ohne `rag`, `postgres` und `connectors`), für Python 3.11 und 3.12. Daraus installieren das Backend-Docker-Image und die CI, und mit `pip install -r requirements.lock` stellen Sie dieselbe Umgebung von Hand wieder her.

Die editierbare Installation ist Absicht: Der Befehl folgt dem Checkout, `git pull` eingeschlossen, statt eine Kopie einzufrieren. In site-packages landet ein einziges Paket, `agents_hub`, dessen einzige Aufgabe es ist, den Checkout auf `sys.path` zu legen und an das Paket `cli` zu übergeben.

Ganz ohne Installation ist `python -m cli` aus dem Checkout dasselbe Programm, und die beiden Server starten Sie direkt:

```bash
python -m uvicorn dashboard.backend.main:app --host 0.0.0.0 --port 8000
cd dashboard/frontend && npm run dev -- --host 0.0.0.0 --port 5173
```

## Geführte Einrichtung (`ah setup`)

`ah setup` fragt sich durch die ganze Installation, so wie der erste Start von OpenClaw, und schreibt nichts, bis Sie eine Übersicht über jeden Wert bestätigt haben:

1. **Installation**: dieser Checkout (Weg C), Docker aus den veröffentlichten Images (Weg A, in einen Ordner Ihrer Wahl), Compose aus diesem Checkout (Weg B) oder ein Hub, der bereits anderswo läuft. Danach QuickStart (nur, was sich nicht erraten lässt: Den Rest richtet der Assistent mit Ihnen ein, siehe [nach der Installation](#nach-der-installation-der-assistent-übernimmt)) oder Advanced.
2. **Datenbank**: SQLite, ein Postgres in einem Docker-Container, den das Setup für Sie startet (auf diesem Rechner) oder im selben Stack (die Docker-Wege, Profil `postgres`), oder ein Postgres, das Sie schon haben und das vor der Übernahme getestet wird. Ein vorhandener SQLite-Zustand lässt sich hinüberkopieren (`ah db migrate`).
3. **Zugang**: keine Anmeldung, ein gemeinsames Token (wird erzeugt) oder Konten: der erste Administrator und weitere Personen, in Advanced mit Single Sign-On. `AGENTS_HUB_SECRET_KEY` wird einmal erzeugt und von einem späteren Lauf nie rotiert.
4. **Anbieter und Modelle**: OpenAI, Anthropic, Google, Ollama, LM Studio. Jeder Schlüssel und jede Adresse wird geprüft, indem der Anbieter nach seiner Modellliste gefragt wird. Das Standardmodell wird aus Voreinstellungen gewählt, die gegen diese Liste abgeglichen werden (ausgewogen, stärkstes, schnellstes), aus der ganzen Liste oder per Eingabe. Auf diesem Rechner werden die Voreinstellungen außerdem im Katalog von [Modelle](models.md) aktiviert, das Standardmodell mit einem Stern.
5. **Stimme des Assistenten**: womit der [Assistent](assistant.md#voice) Sie hört und womit er seine Antworten vorliest, gespeichert als Transkriptions- und Sprachmodelle des Workspace `default` (jeder persönliche Workspace greift darauf zurück). Zur Wahl stehen ein Cloud-Anbieter, den Sie in Schritt 4 gewählt haben (OpenAI: `gpt-4o-mini-tts` und `gpt-4o-mini-transcribe` mit ihren Preisen; Google: Gemini TTS und Gemini Flash), mit einer seiner Stimmen; die eigene Modell-Runtime des Hubs (Whisper small oder large-v3 turbo zum Hören; Piper auf Russisch, Englisch oder Deutsch, Kokoro, Supertonic in 31 Sprachen oder Kitten auf Englisch zum Sprechen; kostenlos, 0,5 bis 2 GB zum Herunterladen); nur der Browser (seine eigene Erkennung und Stimme, nichts auf dem Server); oder vorerst nichts. QuickStart überlässt das dem Assistenten.
6. **Funktionen**: in Advanced der Demo-Workspace, der Dashboard-Port, wo die Agenten laufen, Websuche, RAG und, für Compose, der Browser und Redis. QuickStart überlässt die Demo und die Websuche dem Assistenten.
7. **Übersicht** über jede Einstellung, die geschrieben wird (Geheimnisse maskiert), und jede Aktion, die ausgeführt wird.
8. **Anwenden**: installiert fehlende Python-Extras, startet Postgres, schreibt die `.env` (die vorherige bleibt als `.env.bak-<time>` erhalten), legt die Konten an, füllt den Katalog, setzt die Stimme des Assistenten und bietet an, den Hub zu starten (`ah up`, oder `docker compose up -d` und anschließend die Konten anlegen und die Stimme über die API setzen). Für eine Stimme auf der Runtime des Hubs startet es die Runtime, installiert die fehlenden Engines, lädt die fehlenden Modelle herunter und verfolgt jeden Job bis zum Ende. Unter Docker wartet das auf den Stack: Wird er nicht gestartet, bleibt die Stimme einem späteren Lauf oder der Seite Modelle überlassen. Nach einer Docker-Einrichtung kann es `ah` mit einem persönlichen API-Schlüssel des neuen Administrators auf den Stack richten.

Eine Antwortdatei, die `voice.mode` oder `demo` nennt, wendet beides auch in QuickStart an.

### Nach der Installation: Der Assistent übernimmt

Die Konsole (oder der Browser) erledigt nur, was der Assistent nicht selbst tun kann: ein Konto und ein Modell, mit dem er denken kann. Wenn sich das Dashboard zum ersten Mal öffnet, fragt das Willkommensfenster entweder nach diesem Modell (ein Schlüssel von OpenAI, Anthropic oder Google, oder ein Modellserver, der schon auf diesem Rechner läuft, vor dem Speichern beim Anbieter geprüft) oder bietet, sobald eines vorhanden ist, **Mit dem Assistenten sprechen** oder **Dem Assistenten schreiben** an. Von dort führt der [Assistent](assistant.md#guided-setup) Sie durch den Rest der Einrichtung, Schritt für Schritt, per Stimme oder Text: das Standardmodell, seine eigene Stimme, die Websuche, den Demo-Workspace, das Team, den Zustand des Hubs, dann einen ersten Chat, einen Kanal, Konten, einen eigenen Agenten, eine Aufgabe und etwas, das von selbst läuft. Jede Änderung macht er erst nach Ihrem Ja auf einer Karte. Schlüssel geben Sie in eine Karte ein, sie werden nie im Gespräch gesagt oder getippt, und die Seite zu jedem Schritt öffnet sich neben dem Gespräch. Die Plakette **Einrichtung** in der Kopfzeile zeigt, wie weit er gekommen ist, und bringt Sie dorthin zurück.

`./install.sh` startet sie bei einer Erstinstallation aus einem Terminal von selbst (`--no-setup` überspringt sie, `--setup` führt sie bei einer Neuinstallation aus). Führen Sie sie erneut aus, wann immer sich eine Einstellung ändern soll: Jede Frage hat dann den geltenden Wert als Vorgabe, und eine leere Antwort bei einem Schlüssel behält den aktuellen Schlüssel.

```bash
ah setup                       # fragen
ah setup --shape docker --dir ~/agents-hub
ah setup --dry-run             # nach der Übersicht anhalten
ah setup --answers setup.json  # unbeaufsichtigt, siehe unten
ah setup --disconnect          # einen Hub vergessen, den die Remote-Form gespeichert hat
```

Für eine unbeaufsichtigte Installation hat jede Frage einen Schlüssel, und eine Antwortdatei gibt die Antworten unter diesem Schlüssel an. Was sie weglässt, bekommt die Vorgabe der Frage, und eine fehlende Pflichtantwort bricht mit ihrem Namen ab:

```json
{
  "shape": "docker",
  "dir": "/srv/agents-hub",
  "database": "postgres-bundled",
  "auth": "multi",
  "admin": {"username": "admin", "password": "change-me-now"},
  "users": [{"username": "dana", "role": "member"}],
  "providers": {"anthropic": {"api_key": "sk-ant-...", "model": "claude-sonnet-5"}},
  "voice": {"mode": "local", "speech": "piper-en", "transcription": "whisper-small"},
  "demo": false,
  "start": true
}
```

`voice.mode` ist `cloud` (mit `voice.provider`, wenn zwei passen, und `voice.voice`), `local` (`voice.speech`: `piper-ru`, `piper-en`, `piper-de`, `kokoro`, `supertonic` oder `kitten`; `voice.transcription`: `whisper-small` oder `whisper-turbo`), `browser` oder `skip`. Weggelassene Passwörter werden erzeugt und am Ende einmal ausgegeben. Die Remote-Form speichert die Adresse des Hubs und eine Zugangsberechtigung in der eigenen Zustandsdatei der CLI (`~/.config/agents-hub/cli.json`); `AGENTS_HUB_URL` in der Umgebung hat weiterhin Vorrang.

## Konfiguration

Die `.env` wird beim Start gelesen. Das Minimum sind ein Anbieter und ein Schlüssel:

```env
DEFAULT_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5
AGENT_EXECUTION_MODE=local
```

`.env.example` listet jede Variable mit ihrem Standardwert auf. Das meiste lässt sich danach im Dashboard bearbeiten, und die Aufteilung sollten Sie einmal kennenlernen: [settings](settings.md) enthält die Zugangsdaten, [models](models.md) den Katalog, also welche Modelle angeboten werden, was jedes kostet und welches pro Anbieter das Standardmodell ist.

## Prüfen, ob es funktioniert hat

```bash
curl http://localhost:8000/api/health   # Datenbank, Hintergrunddienste, Größe des Zustands
ah config                               # mit welchem Dienst die CLI spricht
ah agent list                           # die mitgelieferten System-Agenten
```

Unter Docker (Wege A und B) liegt die API auch hinter dem nginx des Dashboards, daher antwortet `curl http://localhost:8080/api/health` genauso. Öffnen Sie `http://localhost:5173` (unter Docker `http://localhost:8080`), wählen Sie im Chat einen Agenten und senden Sie etwas. Eine Antwort bedeutet, dass der Anbieterschlüssel, der Modellkatalog und die gesamte Run-Pipeline funktionieren. Schlägt der Run stattdessen fehl, sagt Ihnen [service-health](service-health.md), welcher Teil schuld ist.

## Wo der Zustand liegt

Alles, was der Dienst schreibt, landet unter `.agents_hub/` im Checkout: `agents_hub.db` (SQLite, WAL: das Agentenregister, der Modellkatalog, Flows, Runs, Aufgaben, Sessions, Speicherpools und jeder andere Datensatz), die Workspace-Ordner, Run-Logs und erzeugte Ansichten. Löschen Sie diesen Ordner, wird die Installation zurückgesetzt. Es ist auch der Ordner, den Sie sichern ([backup](backup.md)). Bei Weg A liegt derselbe Baum auf dem Volume `agents_hub_data`, das unter `/data` eingebunden ist (`AGENTS_HUB_ROOT`). Ist `AGENTS_HUB_DATABASE_URL` gesetzt, liegt die Datenbank statt in der Datei in Postgres ([scaling](scaling.md), mit `ah db migrate` ziehen Sie eine vorhandene um). Der Rest des Ordners bleibt, wo er ist. Die Prompts der Agenten sind genauso getrennt: Der ausgelieferte Text der Systemagenten liegt in `agents/definitions/<id>/` im Repository, so wie git ihn verfolgt, und der Dienst schreibt dort nie. Die `instructions.md` (mit `capabilities.md` und `usage.md`) jedes eigenen Agenten und jede Änderung am Prompt eines Systemagenten landen in `.agents_hub/definitions/<id>/`. Eine Datei dort überdeckt die gleichnamige ausgelieferte Datei. Eine Änderung an einem Systemagenten übersteht so ein Update. **Ausgelieferten Text wiederherstellen** im Tab Config des Agenten (`DELETE /api/agents/<id>/definition/edits`) bringt den ausgelieferten Text zurück und behält den geänderten unter Versionen.

## Aktualisieren

| Weg | Aktualisieren |
| --- | --- |
| A | `docker compose pull && docker compose up -d` |
| B | `git pull && docker compose up -d --build` |
| C | `git pull && ./install.sh` (übernimmt neue Abhängigkeiten; die `.env` bleibt unberührt) |
| D | `git pull && pip install -e ".[backend,agents]"`, `npm install` im Dashboard |

Eine editierbare Installation braucht für Code-Änderungen keine Neuinstallation. Schema-Änderungen wenden sich von selbst an: Die erste Verbindung in einem beliebigen Prozess stellt das Schema sicher, daher kann ebenso gut das Backend oder ein CLI-Befehl sie ausführen. Eine SQLite-Datenbank wird vor der Migration archiviert, und ein Release mit Migrationen lässt sich ohne dieses Archiv nicht zurückrollen: Lesen Sie zuerst die **Upgrade notes** des Releases im [changelog](changelog.md) und für das vollständige Vorgehen [deployment](deployment.md) ("Upgrading", "Rolling back").

## Wenn es nicht startet

- **Das Backend beendet sich sofort.** Die virtuelle Umgebung ist nicht aktiv, oder die Extras sind nicht installiert. Führen Sie `pip install -e ".[backend,agents]"` noch einmal aus und lesen Sie die Ausgabe.
- **Das Dashboard erreicht die API nicht.** Es zielt auf `http://localhost:8000/api`. Prüfen Sie, ob das Backend auf diesem Port läuft und keine lokale CORS-Überschreibung es blockiert.
- **Runs schlagen sofort nach dem Start fehl.** Ein fehlender oder falscher Anbieterschlüssel, ein Modell, das auf der Seite [Modelle](models.md) nicht aktiviert ist, oder `AGENT_EXECUTION_MODE=docker` ohne erreichbaren Docker-Daemon.
- **`ah` wird nicht gefunden.** Die venv liegt nicht im PATH, und der Shell-Hook ist nicht installiert. Führen Sie `ah shell-init --install` aus der venv aus oder rufen Sie das Skript mit seinem absoluten Pfad auf. Siehe [cli](cli.md).
- **Weg A: `unable to open database file`.** Der Schritt `init`, der das Volume an den Benutzer des Backends übergibt, ist nicht gelaufen. `docker compose up -d` führt ihn erneut aus (er ist idempotent), und `docker compose logs init` zeigt, warum er fehlgeschlagen ist.
- **Weg A: `.env` ist ein Verzeichnis.** Docker hat es angelegt, weil die Datei beim ersten Start des Stacks fehlte. Führen Sie `docker compose down` aus, dann `rmdir .env`, speichern Sie die Vorlage als `.env` und starten Sie noch einmal.
