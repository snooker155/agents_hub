# Den Dienst installieren und starten

Es gibt drei Wege, den Dienst zum Laufen zu bringen. Der erste ist Docker Compose aus einem Checkout, die letzten beiden sind für die Arbeit am Code. Alle erzeugen denselben Dienst, und der Rest dieser Seite (Konfiguration, wo der Zustand liegt, Aktualisieren, was bei einem Fehlstart zu prüfen ist) gilt für jeden von ihnen.

| Weg | Sie brauchen | Sie erhalten |
| --- | --- | --- |
| [A. Docker Compose aus einem Checkout](#weg-a-docker-compose-aus-einem-checkout) | Docker, git | Das Dashboard auf `:8080` und die API auf `:8000`, aus dem Quellcode gebaut, dazu Container pro Agent |
| [B. Das Installationsprogramm](#weg-b-das-installationsprogramm) | Python 3.11+, Node 22+ | Eine virtuelle Umgebung, den Befehl `ah` in Ihrem PATH, das Dashboard mit Hot Reload |
| [C. Von Hand](#weg-c-von-hand) | Python 3.11+, Node 22+ | Weg B in seine Einzelteile zerlegt, für eine andere Form |

Jeder der Wege A bis C lässt sich auch einrichten, indem Sie Fragen beantworten: siehe die [geführte Einrichtung](#geführte-einrichtung-ah-setup) weiter unten.

## Voraussetzungen

| Werkzeug | Version | Nötig für |
| --- | --- | --- |
| Docker + Compose | jede aktuelle | Weg A sowie die Agentenausführung in Docker bei B und C |
| Python | 3.11+ | Wege B und C: Backend, CLI, Agent-Runner |
| Node.js + npm | 22+ | Wege B und C: das Dashboard |
| Ein Anbieterschlüssel | | OpenAI, Anthropic, Google oder ein lokales Ollama / LM Studio. Lässt sich nach dem Start in den Einstellungen hinzufügen |

## Weg A: Docker Compose aus einem Checkout

```bash
git clone -b lite https://github.com/snooker155/agents_hub.git
cd agents_hub
docker compose up --build              # Backend :8000, Dashboard :8080
```

Die `.env` ist hier optional: Der Stack startet ohne Anbieterschlüssel, und Sie fügen sie in den Einstellungen hinzu. `BACKEND_PORT` und `WEB_PORT` verschieben die veröffentlichten Ports, und das Dashboard folgt ihnen, weil es mit seinem eigenen Origin spricht. Der Checkout ist in das Backend eingebunden, daher landet der Zustand wie bei einer lokalen Installation in `.agents_hub/` darunter. Aktualisieren heißt `git pull`, gefolgt von `docker compose up -d --build`. Der Docker-Socket ist eingebunden, daher können Agenten in eigenen Containern laufen (`AGENT_EXECUTION_MODE=docker`, [containers](containers.md)). `WITH_RAG=true` in der `.env` baut das Image mit dem RAG-Stack.

Auch Compose legt `ah` nicht auf den Host: `docker compose exec backend ah ...` führt ihn im Container aus, und ein Client auf dem Host ist ein eigener Schritt, siehe [der Befehl `ah` auf jedem Weg](#der-befehl-ah-auf-jedem-weg).

Das Dashboard ist das gebaute Bundle hinter nginx, der auch `/api` weiterleitet.

Compose hat keinen Vite-Dienst: Für eine HMR-Schleife beim Bearbeiten von Frontend-Code starten Sie `npm run dev` auf dem Host wie bei Weg B. Das Image bauen Sie neu (`docker compose up --build frontend`), wenn die Änderung im Container ankommen soll.

## Weg B: Das Installationsprogramm

```bash
git clone -b lite https://github.com/snooker155/agents_hub.git
cd agents_hub
./install.sh
```

Es legt `.venv` an, installiert den Dienst und den Befehl `ah` editierbar, kopiert `.env.example` nach `.env`, falls Sie noch keine `.env` haben, installiert die npm-Pakete des Dashboards und schreibt die Shell-Integration in Ihre Startdatei. Sie können es gefahrlos erneut ausführen: Eine vorhandene `.env` wird nie überschrieben, und der Shell-Block wird an Ort und Stelle neu geschrieben, statt noch einmal angehängt zu werden.

Flags: `--no-frontend` (der Dienst ohne die npm-Pakete des Dashboards), `--cli-only` (nur der Client, zur Verwendung mit `AGENTS_HUB_URL`, und ebenfalls ohne Dashboard), `--with-rag` (fügt die RAG-Extras hinzu, die torch mitbringen), `--with-demo` (schaltet `DEMO_WORKSPACE` in der `.env` ein, sodass der Demo-Workspace beim ersten Start des Dienstes angelegt wird, siehe [demo](demo.md)), `--setup` / `--no-setup` (die [geführte Einrichtung](#geführte-einrichtung-ah-setup), die sonst bei einer Erstinstallation aus einem Terminal läuft und dann die `.env` selbst schreibt), `--no-venv`, `--no-shell`, `--venv PATH`, `--python PATH`.

Danach, in einem neuen Terminal:

```bash
ah up                       # API auf :8000, Dashboard auf :5173
```

## Weg C: Von Hand

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[backend,agents]"     # Dienst + der Befehl ah
cp .env.example .env
cd dashboard/frontend && npm install && cd ../..
```

`pip install -e .` allein installiert nur den Terminal-Client und seine drei Abhängigkeiten. Die Extras werden aus den Requirement-Dateien im Repository gelesen, sodass `[backend]`, `[agents]` und `[rag]` mit ihnen im Gleichschritt bleiben.

`requirements.lock` legt die genaue Auflösung dieser Requirement-Dateien fest (ohne `rag`), für Python 3.11 und 3.12. Daraus installieren das Backend-Docker-Image und die CI, und mit `pip install -r requirements.lock` stellen Sie dieselbe Umgebung von Hand wieder her.

Die editierbare Installation ist Absicht: Der Befehl folgt dem Checkout, `git pull` eingeschlossen, statt eine Kopie einzufrieren. In site-packages landet ein einziges Paket, `agents_hub`, dessen einzige Aufgabe es ist, den Checkout auf `sys.path` zu legen und an das Paket `cli` zu übergeben.

Ganz ohne Installation ist `python -m cli` aus dem Checkout dasselbe Programm, und die beiden Server starten Sie direkt:

```bash
python -m uvicorn dashboard.backend.main:app --host 0.0.0.0 --port 8000
cd dashboard/frontend && npm run dev -- --host 0.0.0.0 --port 5173
```

## Der Befehl `ah` auf jedem Weg

`ah` erreicht den Dienst auf eine von zwei Arten ([cli](cli.md), "Two ways it reaches the service"): **direkt**, in einem Prozess, der den Checkout, dessen Python-Abhängigkeiten und das Zustandsverzeichnis hat, oder **über REST**, zu einem laufenden Backend, das `AGENTS_HUB_URL` nennt. Welche davon verfügbar ist, hängt vom Weg ab:

| Weg | `ah` auf dem Host | `ah` im Backend-Container |
| --- | --- | --- |
| A. Compose aus einem Checkout | Nicht installiert. Client hinzufügen (unten), REST zu `http://localhost:8080` oder `:8000` | `docker compose exec backend ah ...`, direkt |
| B. Installationsprogramm | Im PATH in einem neuen Terminal, direkt | |
| C. Von Hand | In der aktivierten venv nach `pip install -e ...`, direkt; ohne Installation `python -m cli` aus dem Checkout | |

Der direkte Modus auf dem Host erreicht keine Docker-Installation. Bei Weg A gehört die Datenbank im eingebundenen Checkout dem Backend im Container: Ein zweiter Prozess auf dem Host bräuchte dieselben Abhängigkeiten und Einstellungen, und unter macOS und Windows halten die Sperren von SQLite über die Dateifreigabe von Docker Desktop nicht. Bei Weg A spricht der Host deshalb REST, und die Befehle, die nur direkt funktionieren, laufen im Container: `ah db ...`. `ah up` und `ah server start` starten einen Dienst auf diesem Rechner und sind für eine Docker-Installation nicht gedacht.

### `ah` zu einer Docker-Installation hinzufügen

**Im Container** ist nichts hinzuzufügen: Das Backend-Image bringt `ah` mit (Images nach 0.12.0; in 0.12.0 und älter ist `python -m cli` dasselbe Programm). Er läuft direkt, auf derselben Datenbank und demselben Volume wie das Backend:

```bash
docker compose exec backend ah doctor
docker compose exec backend ah db backup --to /data/backups
alias ah='docker compose -f ~/agents-hub/docker-compose.yml exec backend ah'
```

Mit diesem Alias bedeutet `ah` in jedem Ordner des Hosts den Befehl im Container; Pfade, die er bekommt, sind dann Pfade im Container.

**Auf dem Host** braucht ein Client über REST einen Checkout (Weg A hat schon einen) und Python 3.11+, aber keine der Abhängigkeiten des Dienstes:

```bash
git clone -b lite https://github.com/snooker155/agents_hub.git && cd agents_hub
./install.sh --cli-only        # .venv mit ah und seinen drei Abhängigkeiten, der Shell-Hook
export AGENTS_HUB_URL=http://localhost:8080
ah config                      # nennt den Dienst, mit dem er spricht
ah agent list
```

Eine frische Installation fragt nach keinen Zugangsdaten. Pfade über REST löst das Backend auf, daher muss ein Ordner des Hosts zuerst in den Container eingebunden werden ([cli](cli.md), "Against a containerized backend").

Ohne Python auf dem Host baut `scripts/build_cli_binary.sh` auf einem Rechner, der es hat, einen `ah` als einzelne Datei, nur für REST, die sich überallhin kopieren lässt ([cli](cli.md), "A binary, for REST mode without a venv").

## Geführte Einrichtung (`ah setup`)

`ah setup` fragt sich durch die ganze Installation, so wie der erste Start von OpenClaw, und schreibt nichts, bis Sie eine Übersicht über jeden Wert bestätigt haben:

1. **Installation**: dieser Checkout (Weg B) oder Compose aus diesem Checkout (Weg A). Danach QuickStart (nur, was sich nicht erraten lässt) oder Advanced.
2. **Anbieter und Modelle**: OpenAI, Anthropic, Google, Ollama, LM Studio. Jeder Schlüssel und jede Adresse wird geprüft, indem der Anbieter nach seiner Modellliste gefragt wird. Das Standardmodell wird aus Voreinstellungen gewählt, die gegen diese Liste abgeglichen werden (ausgewogen, stärkstes, schnellstes), aus der ganzen Liste oder per Eingabe. Auf diesem Rechner werden die Voreinstellungen außerdem im Katalog von [Modelle](models.md) aktiviert, das Standardmodell mit einem Stern.
3. **Funktionen**: in Advanced der Demo-Workspace, der Dashboard-Port, wo die Agenten laufen und RAG.
4. **Übersicht** über jede Einstellung, die geschrieben wird (Geheimnisse maskiert), und jede Aktion, die ausgeführt wird.
5. **Anwenden**: installiert fehlende Python-Extras, schreibt die `.env` (die vorherige bleibt als `.env.bak-<time>` erhalten), füllt den Katalog und bietet an, den Hub zu starten (`ah up` oder `docker compose up -d`).

Eine Antwortdatei, die `demo` nennt, wendet es auch in QuickStart an.

### Der erste Start im Browser

Eine neue
Installation öffnet statt der App einen eigenen Einrichtungsbildschirm: ohne
Seitenleiste und Kopfleiste, eine Entscheidung pro Bildschirm, wie bei einem
neuen Telefon. Der Reihe nach: Begrüßung, Sprache, hell oder dunkel,
ein Modellschlüssel und das Standardmodell, persönlicher Speicher und der
Demo-Arbeitsbereich, dann eine Übersicht mit dem Weg hinein (**Agents Hub
verwenden** oder **Rundgang starten**).

Nur das Modell ist Pflicht: ein Schlüssel von OpenAI, Anthropic oder Google
(vor dem Speichern beim Anbieter geprüft), ein Modellserver, der auf diesem
Rechner schon läuft (Ollama, LM Studio; seine Modelle stehen zur Wahl).
Jeder spätere Bildschirm hat **Nicht jetzt**, und ein
Bildschirm, dessen Antwort schon feststeht (ein Schlüssel aus `ah setup`), sagt das nur. Als Ganzes
überspringen lässt sich die Einrichtung nicht.

Der Schritt wird im Hub gespeichert: nach einem Neuladen oder in einem
anderen Browser geht es an derselben Stelle weiter, und ein neuer Browser
übernimmt Sprache und Darstellung. Abgeschlossen erscheint sie nicht wieder;
**Einstellungen, Ersteinrichtung** startet sie bewusst erneut. Eine
Installation, die vor dem Update schon genutzt wurde (ein Chat), hält sie nicht an.

`./install.sh` startet sie bei einer Erstinstallation aus einem Terminal von selbst (`--no-setup` überspringt sie, `--setup` führt sie bei einer Neuinstallation aus). Führen Sie sie erneut aus, wann immer sich eine Einstellung ändern soll: Jede Frage hat dann den geltenden Wert als Vorgabe, und eine leere Antwort bei einem Schlüssel behält den aktuellen Schlüssel.

```bash
ah setup                       # fragen
ah setup --shape docker --dir ~/agents-hub
ah setup --dry-run             # nach der Übersicht anhalten
ah setup --answers setup.json  # unbeaufsichtigt, siehe unten
```

Für eine unbeaufsichtigte Installation hat jede Frage einen Schlüssel, und eine Antwortdatei gibt die Antworten unter diesem Schlüssel an. Was sie weglässt, bekommt die Vorgabe der Frage, und eine fehlende Pflichtantwort bricht mit ihrem Namen ab:

```json
{
  "shape": "docker",
  "dir": "/srv/agents-hub",
  "providers": {"anthropic": {"api_key": "sk-ant-...", "model": "claude-sonnet-5"}},
  "demo": false,
  "start": true
}
```

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

Unter Docker (Weg A) liegt die API auch hinter dem nginx des Dashboards, daher antwortet `curl http://localhost:8080/api/health` genauso. Öffnen Sie `http://localhost:5173` (unter Docker `http://localhost:8080`), wählen Sie im Chat einen Agenten und senden Sie etwas. Eine Antwort bedeutet, dass der Anbieterschlüssel, der Modellkatalog und die gesamte Run-Pipeline funktionieren. Schlägt der Run stattdessen fehl, sagt Ihnen [service-health](service-health.md), welcher Teil schuld ist.

## Wo der Zustand liegt

Alles, was der Dienst schreibt, landet unter `.agents_hub/` im Checkout: `agents_hub.db` (SQLite, WAL: das Agentenregister, der Modellkatalog, Flows, Runs, Aufgaben, Sessions, Speicherpools und jeder andere Datensatz), die Workspace-Ordner, Run-Logs und erzeugte Ansichten. Löschen Sie diesen Ordner, wird die Installation zurückgesetzt. Es ist auch der Ordner, den Sie sichern ([backup](backup.md)). Die Prompts der Agenten sind genauso getrennt: Der ausgelieferte Text der Systemagenten liegt in `agents/definitions/<id>/` im Repository, so wie git ihn verfolgt, und der Dienst schreibt dort nie. Die `instructions.md` (mit `capabilities.md` und `usage.md`) jedes eigenen Agenten und jede Änderung am Prompt eines Systemagenten landen in `.agents_hub/definitions/<id>/`. Eine Datei dort überdeckt die gleichnamige ausgelieferte Datei. Eine Änderung an einem Systemagenten übersteht so ein Update. **Ausgelieferten Text wiederherstellen** im Tab Config des Agenten (`DELETE /api/agents/<id>/definition/edits`) bringt den ausgelieferten Text zurück und behält den geänderten unter Versionen.

## Aktualisieren

| Weg | Aktualisieren |
| --- | --- |
| A | `git pull && docker compose up -d --build` |
| B | `git pull && ./install.sh` (übernimmt neue Abhängigkeiten; die `.env` bleibt unberührt) |
| C | `git pull && pip install -e ".[backend,agents]"`, `npm install` im Dashboard |

Eine editierbare Installation braucht für Code-Änderungen keine Neuinstallation. Schema-Änderungen wenden sich von selbst an: Die erste Verbindung in einem beliebigen Prozess stellt das Schema sicher, daher kann ebenso gut das Backend oder ein CLI-Befehl sie ausführen. Eine SQLite-Datenbank wird vor der Migration archiviert, und ein Release mit Migrationen lässt sich ohne dieses Archiv nicht zurückrollen: Lesen Sie zuerst die **Upgrade notes** des Releases im [changelog](changelog.md) und für das vollständige Vorgehen [deployment](deployment.md) ("Upgrading", "Rolling back").

## Wenn es nicht startet

- **Das Backend beendet sich sofort.** Die virtuelle Umgebung ist nicht aktiv, oder die Extras sind nicht installiert. Führen Sie `pip install -e ".[backend,agents]"` noch einmal aus und lesen Sie die Ausgabe.
- **Das Dashboard erreicht die API nicht.** Es zielt auf `http://localhost:8000/api`. Prüfen Sie, ob das Backend auf diesem Port läuft und keine lokale CORS-Überschreibung es blockiert.
- **Runs schlagen sofort nach dem Start fehl.** Ein fehlender oder falscher Anbieterschlüssel, ein Modell, das auf der Seite [Modelle](models.md) nicht aktiviert ist, oder `AGENT_EXECUTION_MODE=docker` ohne erreichbaren Docker-Daemon.
- **`ah` wird nicht gefunden.** Die venv liegt nicht im PATH, und der Shell-Hook ist nicht installiert. Führen Sie `ah shell-init --install` aus der venv aus oder rufen Sie das Skript mit seinem absoluten Pfad auf. Siehe [cli](cli.md). Bei Weg A hat der Host keinen `ah`, bis Sie einen hinzufügen, siehe [der Befehl `ah` auf jedem Weg](#der-befehl-ah-auf-jedem-weg).
