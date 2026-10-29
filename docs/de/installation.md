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
docker compose exec backend ah db backup --to /data/backups
docker compose logs -f backend
```

Auf dem Host gibt es bei diesem Weg keinen Befehl `ah`: Er liegt im Backend-Container, und ein Client auf dem Host ist ein eigener Schritt, siehe [der Befehl `ah` auf jedem Weg](#der-befehl-ah-auf-jedem-weg).

Was diese Form weglässt: Der Docker-Socket ist nicht eingebunden, daher können Agenten keine eigenen Container bekommen ([containers](containers.md)). Der Sandbox-Anbieter `docker` und der Browser-Dienst brauchen Weg B. Das Image ohne das Suffix `-rag` enthält keinen Embedding-Stack, deshalb bleibt `RAG_VECTOR_DB` auf `none`, solange Sie nicht das Tag mit `-rag` festlegen.

## Weg B: Docker Compose aus einem Checkout

```bash
git clone https://github.com/snooker155/agents_hub.git
cd agents_hub
docker compose up --build              # Backend :8000, Dashboard :8080
```

Die `.env` ist hier optional: Der Stack startet ohne Anbieterschlüssel, und Sie fügen sie in den Einstellungen hinzu. `BACKEND_PORT` und `WEB_PORT` verschieben die veröffentlichten Ports, und das Dashboard folgt ihnen, weil es mit seinem eigenen Origin spricht. Der Checkout ist in das Backend eingebunden, daher landet der Zustand wie bei einer lokalen Installation in `.agents_hub/` darunter. Aktualisieren heißt `git pull`, gefolgt von `docker compose up -d --build`. Der Docker-Socket ist eingebunden, daher können Agenten in eigenen Containern laufen (`AGENT_EXECUTION_MODE=docker`, [containers](containers.md)). `WITH_RAG=true` in der `.env` baut das Image mit dem RAG-Stack.

Auch Compose legt `ah` nicht auf den Host: `docker compose exec backend ah ...` führt ihn im Container aus, und ein Client auf dem Host ist derselbe eigene Schritt wie bei Weg A, siehe [der Befehl `ah` auf jedem Weg](#der-befehl-ah-auf-jedem-weg).

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

### In eine vorhandene Umgebung

Weg C hängt nicht an der `.venv`, die er anlegt. Jede Umgebung mit Python 3.11+, die Sie ohnehin benutzen, eine conda-Umgebung, eine pyenv-Version oder eine eigene venv, nimmt dieselbe Installation an. Aktivieren Sie sie und führen Sie im Wurzelverzeichnis des Checkouts aus:

```bash
conda activate myenv                   # oder was Ihre Umgebung aktiviert
pip install -e ".[backend,agents]"     # Dienst + der Befehl ah, in diese Umgebung
ah config                              # direkter Modus, Zustand unter diesem Checkout
```

Damit liegt `ah` im `bin` der Umgebung und das Paket `agents_hub` editierbar in ihren site-packages. Drei Dinge sind zu wissen:

- **Editierbar (`-e`) ist Pflicht, keine Vorliebe.** Das installierte Paket findet den Checkout über seinen eigenen Speicherort. `pip install .` ohne `-e` kopiert es in die site-packages, und der Befehl bricht sofort mit `No module named 'cli'` ab.
- **`ah` ist nur im PATH, solange die Umgebung aktiv ist.** Damit der Befehl in jeder Shell verfügbar ist, mit oder ohne Aktivierung, installieren Sie den Shell-Hook einmal aus der aktiven Umgebung: `ah shell-init --install`. Der geschriebene Block enthält den absoluten Pfad zu `ah` dieser Umgebung (`ah shell-init zsh` gibt ihn zur Kontrolle aus), sodass ein neues Terminal den Befehl findet, ohne etwas zu aktivieren. Wiederholen Sie das nach einem Umzug des Checkouts oder einem Neuanlegen der Umgebung.
- **Direkter Modus, mit den Paketen dieser Umgebung.** `ah` führt den Dienst im eigenen Prozess aus, auf den Abhängigkeiten der Umgebung, in die er installiert wurde, und liest und schreibt `.agents_hub/` unter dem Checkout genau wie die venv des Installationsprogramms. Auch `ah up` startet das Backend von hier. Darüber hinaus fügt das Installationsprogramm nur die Kopie der `.env`, die npm-Pakete des Dashboards und die geführte Einrichtung hinzu; das decken die übrigen Zeilen von Weg C und [`ah setup`](#geführte-einrichtung-ah-setup) ab.

Aktualisieren wie bei Weg C: `git pull`, danach `pip install -e ".[backend,agents]"` in derselben Umgebung, sobald sich die Requirement-Dateien geändert haben.

**Eine Umgebung, in der der Dienst schon läuft.** Hat die Umgebung bereits jede Abhängigkeit, weil das Backend daraus läuft oder die Requirement-Dateien von Hand installiert wurden, gibt es nichts aufzulösen, nur den Befehl hinzuzufügen:

```bash
pip install -e . --no-deps          # das Paket agents_hub und das Skript ah, sonst nichts
```

`--no-deps` überspringt die Abhängigkeitsauflösung ganz: pip rührt kein installiertes Paket an und lädt nichts herunter. Die Extras sind hier ebenfalls unnötig, und `pip install -e .` ohne `--no-deps` ist genauso sicher, es kostet nur die Zeit, das Vorhandene zu bestätigen. `ah doctor` zeigt danach, ob dem Dienst doch etwas fehlt.

## Der Befehl `ah` auf jedem Weg

`ah` erreicht den Dienst auf eine von zwei Arten ([cli](cli.md), "Two ways it reaches the service"): **direkt**, in einem Prozess, der den Checkout, dessen Python-Abhängigkeiten und das Zustandsverzeichnis hat, oder **über REST**, zu einem laufenden Backend, das `AGENTS_HUB_URL` nennt. Welche davon verfügbar ist, hängt vom Weg ab:

| Weg | `ah` auf dem Host | `ah` im Backend-Container |
| --- | --- | --- |
| A. Veröffentlichte Images | Nicht installiert. Client hinzufügen (unten), REST zu `http://localhost:8080` | `docker compose exec backend ah ...`, direkt |
| B. Compose aus einem Checkout | Nicht installiert. Client hinzufügen (unten), REST zu `http://localhost:8080` oder `:8000` | `docker compose exec backend ah ...`, direkt |
| C. Installationsprogramm | Im PATH in einem neuen Terminal, direkt | |
| D. Von Hand | In der aktivierten venv oder in [jeder Umgebung, in die Sie installiert haben](#in-eine-vorhandene-umgebung), nach `pip install -e ...`, direkt; ohne Installation `python -m cli` aus dem Checkout | |

Der direkte Modus auf dem Host erreicht keine Docker-Installation. Bei Weg A liegt der Zustand auf einem benannten Volume, das der Host nicht öffnen kann. Bei Weg B gehört die Datenbank im eingebundenen Checkout dem Backend im Container: Ein zweiter Prozess auf dem Host bräuchte dieselben Abhängigkeiten und Einstellungen, und unter macOS und Windows halten die Sperren von SQLite über die Dateifreigabe von Docker Desktop nicht. Auf A und B spricht der Host deshalb REST, und die Befehle, die nur direkt funktionieren, laufen im Container: `ah db ...`, `ah worker` und `ah deployment`. `ah up` und `ah server start` starten einen Dienst auf diesem Rechner und sind für eine Docker-Installation nicht gedacht.

### `ah` zu einer Docker-Installation hinzufügen

**Im Container** ist nichts hinzuzufügen: Das Backend-Image bringt `ah` mit (Images nach 0.12.0; in 0.12.0 und älter ist `python -m cli` dasselbe Programm). Er läuft direkt, auf derselben Datenbank und demselben Volume wie das Backend:

```bash
docker compose exec backend ah doctor
docker compose exec backend ah db backup --to /data/backups
alias ah='docker compose -f ~/agents-hub/docker-compose.yml exec backend ah'
```

Mit diesem Alias bedeutet `ah` in jedem Ordner des Hosts den Befehl im Container; Pfade, die er bekommt, sind dann Pfade im Container.

**Auf dem Host** braucht ein Client über REST einen Checkout (Weg B hat schon einen) und Python 3.11+, aber keine der Abhängigkeiten des Dienstes:

```bash
git clone https://github.com/snooker155/agents_hub.git && cd agents_hub
./install.sh --cli-only        # .venv mit ah und seinen drei Abhängigkeiten, der Shell-Hook
export AGENTS_HUB_URL=http://localhost:8080
ah config                      # nennt den Dienst, mit dem er spricht
ah agent list
```

Statt des `export` speichert `ah setup` mit **Connect to a hub that already runs** die Adresse und einen Schlüssel in `~/.config/agents-hub/cli.json`, und die Docker-Formen von `ah setup` bieten dasselbe am Ende an. Eine frische Installation läuft im Modus `single` und fragt nach keinen Zugangsdaten; mit `AUTH_MODE=token` setzen Sie `AGENTS_HUB_API_TOKEN`, mit `multi` einen persönlichen Schlüssel in `AGENTS_HUB_API_KEY` ([cli](cli.md), "Authenticating over `AGENTS_HUB_URL`"). Pfade über REST löst das Backend auf, daher muss ein Ordner des Hosts zuerst in den Container eingebunden werden ([cli](cli.md), "Against a containerized backend").

Ohne Python auf dem Host baut `scripts/build_cli_binary.sh` auf einem Rechner, der es hat, einen `ah` als einzelne Datei, nur für REST, die sich überallhin kopieren lässt ([cli](cli.md), "A binary, for REST mode without a venv").

## Geführte Einrichtung (`ah setup`)

`ah setup` fragt sich durch die ganze Installation, so wie der erste Start von OpenClaw, und schreibt nichts, bis Sie eine Übersicht über jeden Wert bestätigt haben:

1. **Installation**: dieser Checkout (Weg C), Docker aus den veröffentlichten Images (Weg A, in einen Ordner Ihrer Wahl), Compose aus diesem Checkout (Weg B), Kubernetes mit dem Helm-Chart oder ein Hub, der bereits anderswo läuft. Danach QuickStart (nur, was sich nicht erraten lässt: Den Rest richtet der Assistent mit Ihnen ein, siehe [nach der Installation](#nach-der-installation-der-assistent-übernimmt)) oder Advanced. Kubernetes fragt immer alles.
2. **Skalierung** (die Docker-Wege und Kubernetes): ein Backend oder ein Team unter Last: die Topologie `ha` aus [deployment](deployment.md), mit Postgres, Redis und Objektspeicher (im Stack enthalten oder Ihre eigenen, per URL), mehreren API-Replikas hinter dem nginx des Dashboards und mehreren Workern, die die Startwarteschlange abarbeiten. Die Replikazahlen landen in `docker-compose.setup.yml`, einem Overlay, das die `.env` in `COMPOSE_FILE` nennt, sodass ein einfaches `docker compose up` sie liest und die Basisdateien bleiben, wie sie veröffentlicht wurden. In Advanced weitere **Worker-Hosts**: Rechner, die nur Worker gegen das Postgres, Redis und MinIO dieses Hosts laufen lassen (deren Ports werden dann veröffentlicht, hinter erzeugten Passwörtern). Workspaces und Lauf-Dateien sind weiterhin Dateien, daher bindet jeder Rechner ein gemeinsames Verzeichnis (NFS oder Ähnliches) am selben Pfad ein, und der Hub hält seinen Zustand dort; jeder Host bekommt unter `workers/` einen Ordner mit einer Compose-Datei und einer `.env` zum Hinüberkopieren (laufen Agenten dort in Containern, setzen Sie darin `DOCKER_GID` auf die Gruppe des Docker-Sockets jenes Rechners; ein späterer Lauf behält ihn und jeden anderen Schlüssel dieser `.env`, den das Setup nicht schreibt). Den Zustand kopiert das Setup nicht selbst: Zieht eine schon genutzte Installation in ein gemeinsames Verzeichnis oder verlässt sie es wieder (keine Worker-Hosts mehr oder zurück zu einem Backend), lässt es den Stack gestoppt und gibt die Befehle aus, die den Zustand zuerst an den neuen Ort kopieren. Ein späterer Lauf, auch QuickStart, behält Redis, Objektspeicher und Worker-Hosts bei, die gelten; zurück zu einem Backend nimmt alles zurück, was nur die Team-Topologie gesetzt hat. Für Kubernetes: Release, Namespace, Ingress-Host und TLS (cert-manager oder ein vorhandenes Secret), Replikas, Ihr Postgres, Redis und S3-Bucket (oder allein ein ReadWriteMany-Volume) und das Image-Tag; ein späterer Lauf liest `values.yaml` und `secrets.yaml` zurück, sodass jede Frage vorschlägt, was darin steht.
3. **Datenbank**: SQLite, ein Postgres in einem Docker-Container, den das Setup für Sie startet (auf diesem Rechner) oder im selben Stack (die Docker-Wege, Profil `postgres`), oder ein Postgres, das Sie schon haben und das vor der Übernahme getestet wird. Ein vorhandener SQLite-Zustand lässt sich hinüberkopieren (`ah db migrate`). Die Team-Topologie braucht Postgres, Kubernetes Ihr eigenes.
4. **Zugang**: keine Anmeldung, ein gemeinsames Token (wird erzeugt) oder Konten: der erste Administrator und weitere Personen. Single Sign-On in Advanced oder für ein Team: Keycloak im Stack (Profil `keycloak`; das Setup schreibt `keycloak/realm.json` aus dem Realm, gegen den CI sich anmeldet, ohne dessen Demo-Benutzer und mit erzeugtem Client-Secret, und ordnet die Gruppe `hub-admins` des Realms der Administratorrolle zu, sobald der Stack antwortet) oder ein Anbieter, den Sie schon haben. Über HTTPS stellt Caddy Keycloak auf der Domain des Hubs an Port 8443 bereit. Keycloak importiert den Realm nur beim ersten Start, daher aktualisiert das Setup, sobald der Stack antwortet, auch den Client dort (Weiterleitungs-URI, Web-Origins, Secret) über die Admin-API von Keycloak; ohne Start des Stacks gibt ein Lauf, der sie geändert hat, die Schritte für die Admin-Konsole aus. `AGENTS_HUB_SECRET_KEY` wird einmal erzeugt und von einem späteren Lauf nie rotiert, auch nicht von Start over und für Kubernetes; dasselbe gilt für die Passwörter des mitgelieferten Postgres und des Keycloak-Administrators, die deren Volumes vom ersten Start behalten.
5. **Anbieter und Modelle**: OpenAI, Anthropic, Google, Ollama, LM Studio. Jeder Schlüssel und jede Adresse wird geprüft, indem der Anbieter nach seiner Modellliste gefragt wird. Das Standardmodell wird aus Voreinstellungen gewählt, die gegen diese Liste abgeglichen werden (ausgewogen, stärkstes, schnellstes), aus der ganzen Liste oder per Eingabe. Auf diesem Rechner werden die Voreinstellungen außerdem im Katalog von [Modelle](models.md) aktiviert, das Standardmodell mit einem Stern.
6. **Stimme des Assistenten**: womit der [Assistent](assistant.md#voice) Sie hört und womit er seine Antworten vorliest, gespeichert als Transkriptions- und Sprachmodelle des Workspace `default` (jeder persönliche Workspace greift darauf zurück). Zur Wahl stehen ein Cloud-Anbieter, den Sie in Schritt 5 gewählt haben (OpenAI: `gpt-4o-mini-tts` und `gpt-4o-mini-transcribe` mit ihren Preisen; Google: Gemini TTS und Gemini Flash), mit einer seiner Stimmen; die eigene Modell-Runtime des Hubs (Whisper small oder large-v3 turbo zum Hören; Piper auf Russisch, Englisch oder Deutsch, Kokoro, Supertonic in 31 Sprachen oder Kitten auf Englisch zum Sprechen; kostenlos, 0,5 bis 2 GB zum Herunterladen); nur der Browser (seine eigene Erkennung und Stimme, nichts auf dem Server); oder vorerst nichts. QuickStart überlässt das dem Assistenten.
7. **Funktionen**: in Advanced der Demo-Workspace, der Dashboard-Port, wo die Agenten laufen, der Egress-Proxy (`egress_proxy`, vorgeschlagen an, wenn die Agenten in Docker laufen), Websuche, RAG, der Browser-Dienst (beide Docker-Wege) und Redis (Compose, ein Backend). HTTPS auf einer eigenen Domain für die veröffentlichten Images (Caddy und Let's Encrypt) und für ein Team ein Anfragelimit pro Person. QuickStart überlässt die Demo und die Websuche dem Assistenten und behält Browser, Redis, HTTPS und Keycloak bei, wo ein früherer Lauf sie eingeschaltet hat.
8. **Verbundener Hub** (dieser Checkout und beide Docker-Wege): ein anderer Agents Hub, mit dem dieser verbunden wird ([Hub-Verbindung](hub-link.md), [Synchronisierung](hub-sync.md)), über seine URL und einen persönlichen API-Schlüssel Ihres Kontos dort (auf jenem Hub unter Einstellungen, API-Zugriff, Persönliche API-Schlüssel erstellt); beides wird dort geprüft, bevor es übernommen wird. Danach, was die Verbindung tut: **Nur Katalog** (seine Agenten, Skills, Flows, Teams und Schleifen durchsuchen und installieren; nichts von Ihnen wird gesendet), **Zwei-Wege-Synchronisierung** der Workspaces Ihrer Wahl, jeder einem Bereich dort zugeordnet, oder **nur seine Modelle und Agenten**, die Verbindung, wie sie vor den anderen Modi war. Die Synchronisierung fragt dann nach der **Verschlüsselung zum Start**: **jeden Workspace verschlüsseln**, also default und alle anderen Workspaces, auch später hinzugefügte, sodass der verbundene Hub nur Daten speichert, die er nicht lesen kann; oder **vorerst keine Verschlüsselung**, dann kann der verbundene Hub die Daten lesen und anzeigen, und jeder Workspace lässt sich später einzeln verschlüsseln (seine Zeile unter Einstellungen, Entfernter Hub, oder die Einstellungen des Workspace, Hub-Synchronisierung). Der Schritt sagt ausdrücklich, was gilt. Die Verschlüsselung erzeugt eine Passphrase, die am Ende einmal angezeigt wird, oder übernimmt die, die Sie auf einem anderen Gerät schon verwenden; gehen sie und jedes Gerät mit dem Schlüssel verloren, lassen sich die synchronisierten Daten nicht wiederherstellen. In beiden Synchronisierungsmodi ist der Einweg-Spiegel aus, da die Synchronisierung dieselben Daten trägt. In Advanced: der Name dieses Hubs dort, seine Modelle und Agenten hier unabhängig vom Modus und der Katalog neben der Synchronisierung. Für Kubernetes liegt die Verbindung in der Datenbank, daher wird sie in den Einstellungen unter Entfernter Hub gesetzt, sobald der Cluster läuft.
9. **Übersicht** über jede Einstellung, die geschrieben wird (Geheimnisse maskiert), und jede Aktion, die ausgeführt wird.
10. **Anwenden**: installiert fehlende Python-Extras, startet Postgres, schreibt die `.env` (die vorherige bleibt als `.env.bak-<time>` erhalten), legt die Konten an, füllt den Katalog, setzt die Stimme des Assistenten und den verbundenen Hub und bietet an, den Hub zu starten (`ah up`, oder `docker compose up -d` und anschließend die Konten anlegen und die Stimme und die Verbindung über die API setzen). Für eine Stimme auf der Runtime des Hubs startet es die Runtime, installiert die fehlenden Engines, lädt die fehlenden Modelle herunter und verfolgt jeden Job bis zum Ende. Unter Docker wartet das auf den Stack: Wird er nicht gestartet, bleiben die Stimme und die Verbindung einem späteren Lauf oder den Seiten Modelle und Einstellungen überlassen. In diesem Checkout wird die Verbindung direkt in die Datenbank geschrieben, unter Docker über die API des Stacks, sobald er antwortet. Nach einer Docker-Einrichtung kann es `ah` mit einem persönlichen API-Schlüssel des neuen Administrators auf den Stack richten.

Eine Antwortdatei, die `voice.mode` oder `demo` nennt, wendet beides auch in QuickStart an.

### Einrichtung aus Docker

Ohne Python auf dem Host läuft das Setup aus dem Backend-Image. In einem leeren Ordner:

```bash
docker run -it --rm --user 0:0 \
  -v /var/run/docker.sock:/var/run/docker.sock -v "$PWD:$PWD" -w "$PWD" \
  --add-host host.docker.internal:host-gateway \
  ghcr.io/snooker155/agents-hub-backend:latest setup
```

In einem Ordner, der schon eine `docker-compose.yml` enthält (ein Checkout oder die QuickStart-Dateien), erledigt `docker compose run --rm setup` dasselbe: Der Dienst `setup` liegt in einem eigenen Profil, daher startet ihn `docker compose up` nie. Der Ordner wird am selben Pfad eingebunden, sodass der Stack, den das Setup über den Socket startet, dieselben Host-Pfade einbindet. Es bietet die veröffentlichten Images (und Compose, wenn der Ordner ein Checkout ist) oder Kubernetes-Werte an, nie die lokale Form (das Chart wird daneben als `agents-hub-chart/` kopiert, und einen Ordner dieses Namens, den nicht das Setup geschrieben hat, ersetzt es nie), und was es schreibt, Sicherungskopien eingeschlossen, wird dem Besitzer des Ordners zurückgegeben, obwohl der Container als root lief, um den Socket zu nutzen.

### Der erste Start im Browser

Mit einer Person am Hub (`AUTH_MODE=single` oder `token`) öffnet eine neue
Installation statt der App einen eigenen Einrichtungsbildschirm: ohne
Seitenleiste und Kopfleiste, eine Entscheidung pro Bildschirm, wie bei einem
neuen Telefon. Der Reihe nach: Begrüßung, Sprache, hell oder dunkel, ein
Modell, das Standardmodell, die Stimme des Assistenten, Websuche,
persönlicher Speicher und der Demo-Arbeitsbereich, dann eine Übersicht mit
dem Weg hinein (**Agents Hub verwenden**, **Mit dem Assistenten
weitermachen** oder **Rundgang starten**).

Auf dem Bildschirm der Stimme wählt eine OpenAI-Stimme auch ihr Sprachmodell: `gpt-4o-mini-tts` (Standard, am neuesten und natürlichsten), `tts-1` (antwortet am schnellsten) oder `tts-1-hd` (klarer, langsamer). Dann lernen Sie den Assistenten kennen: er sagt laut
Hallo, jede Sprache mit eigener Stimme ([eine Stimme pro Sprache](assistant.md)),
Sie sagen, ob Sie ihn gehört haben, antworten per Stimme und hören die Antwort;
so werden Modell, Sprachausgabe und Spracherkennung in einem Zug geprüft.
Danach hat jeder Bildschirm den Assistenten unten, per Stimme oder Text zu
diesem Bildschirm zu fragen, und der letzte Bildschirm übergibt an ihn, per
Stimme, wenn er zu hören war.

Nur das Modell ist Pflicht: ein Schlüssel von OpenAI, Anthropic oder Google
(vor dem Speichern beim Anbieter geprüft), ein Modellserver, der auf diesem
Rechner schon läuft (Ollama, LM Studio; seine Modelle stehen zur Wahl), oder,
wenn der Hub eine eigene Modellumgebung hat, das fertige lokale Paket, das im
Hintergrund lädt. Jeder spätere Bildschirm hat **Nicht jetzt**, und ein
Bildschirm, dessen Antwort schon feststeht (ein Schlüssel aus `ah setup`,
Websuche über den Schlüssel des Modells), sagt das nur. Als Ganzes
überspringen lässt sich die Einrichtung nicht.

Der Schritt wird im Hub gespeichert: nach einem Neuladen oder in einem
anderen Browser geht es an derselben Stelle weiter, und ein neuer Browser
übernimmt Sprache und Darstellung. Abgeschlossen erscheint sie nicht wieder;
**Einstellungen, Ersteinrichtung** startet sie bewusst erneut. Eine
Installation, die vor dem Update schon genutzt wurde (ein Chat oder eine
begonnene Einrichtung mit dem Assistenten), hält sie nicht an. Im Modus
`multi` wird der erste Administrator auf dem Anmeldebildschirm angelegt und
richtet den Hub über das unten beschriebene Willkommensfenster ein; alle
anderen bekommen die Begrüßung für ein neues Konto.

### Die Begrüßung für ein neues Konto

Im Modus `multi` hat ein Konto, das ein Administrator angelegt hat (auf der
Seite Benutzer, per SSO oder SCIM), nichts einzurichten. Bei der ersten
Anmeldung öffnet sich darum einmal eine eigene Begrüßung statt der App, in der
Sprache, die der Administrator dem Konto gegeben hat (Englisch, wenn keine
gesetzt ist): das Zeichen des Hubs setzt sich zusammen, „Willkommen bei Agents
Hub“ wird nacheinander in jeder Sprache getippt und der Name steht darunter,
danach
zeigt ein Bildschirm, was der Administrator für sie eingerichtet hat, nur zum
Lesen: ihre Arbeitsbereiche und ihre Rolle darin, das Modell (festgelegt, eine
Auswahl aus mehreren oder frei oben im Chat wählbar), den Aufwand, die Stimme,
die Websuche, das persönliche Gedächtnis und das Monatslimit. Sprache und
Darstellung gehören der Person selbst und lassen sich dort gleich ändern; die
gewählte Sprache wird im Konto gespeichert. Vor- und Nachname und Sprache eines
Kontos legt der Administrator auf der Seite Benutzer fest, beim Anlegen oder
später im Fenster **Bearbeiten**.
**Loslegen** öffnet den Chat, **Rundgang starten** den Rundgang.

Der Bildschirm wird pro Person im Hub gespeichert, nach einem Neuladen geht es
an derselben Stelle weiter. Den ersten Administrator und Konten, die es vor
dem Update mit der Begrüßung schon gab, hält sie nicht an.
Der Abschnitt **Dokumentation, Begrüßung** unter den Änderungen zeigt sie
erneut, auch Mitgliedern, die die Einstellungen nicht sehen.

### Nach der Installation: Der Assistent übernimmt

Die Konsole (oder der Browser) erledigt nur, was der Assistent nicht selbst tun kann: ein Konto und ein Modell, mit dem er denken kann. Mit einer Person am Hub erledigt das der erste Start oben, und sein **Mit dem Assistenten weitermachen** führt hierher. Im Modus `multi` fragt, wenn eine Person das Dashboard zum ersten Mal öffnet, das Willkommensfenster entweder nach diesem Modell (ein Schlüssel von OpenAI, Anthropic oder Google, oder ein Modellserver, der schon auf diesem Rechner läuft, vor dem Speichern beim Anbieter geprüft) oder bietet, sobald eines vorhanden ist, **Mit dem Assistenten sprechen** oder **Dem Assistenten schreiben** an. Von dort führt der [Assistent](assistant.md#guided-setup) Sie durch den Rest der Einrichtung, Schritt für Schritt, per Stimme oder Text: das Standardmodell, seine eigene Stimme, die Websuche, den Demo-Workspace, das Team, den Zustand des Hubs, dann einen ersten Chat, einen Kanal, Konten, einen eigenen Agenten, eine Aufgabe und etwas, das von selbst läuft. Jede Änderung macht er erst nach Ihrem Ja auf einer Karte. Schlüssel geben Sie in eine Karte ein, sie werden nie im Gespräch gesagt oder getippt, und die Seite zu jedem Schritt öffnet sich neben dem Gespräch. Die Plakette **Einrichtung** in der Kopfzeile zeigt, wie weit er gekommen ist, und bringt Sie dorthin zurück.

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
  "link": {"enabled": true, "url": "https://hub.example.com", "api_key": "ahk_...",
           "mode": "sync", "sync": {"encryption": "all", "workspaces": ["default"], "generate": true}},
  "start": true
}
```

`voice.mode` ist `cloud` (mit `voice.provider`, wenn zwei passen, und `voice.voice`), `local` (`voice.speech`: `piper-ru`, `piper-en`, `piper-de`, `kokoro`, `supertonic` oder `kitten`; `voice.transcription`: `whisper-small` oder `whisper-turbo`), `browser` oder `skip`. Weggelassene Passwörter werden erzeugt und am Ende einmal ausgegeben. Die Remote-Form speichert die Adresse des Hubs und eine Zugangsberechtigung in der eigenen Zustandsdatei der CLI (`~/.config/agents-hub/cli.json`); `AGENTS_HUB_URL` in der Umgebung hat weiterhin Vorrang.

`link.enabled` schaltet den verbundenen Hub ein (standardmäßig aus), mit `link.url`, `link.api_key` und optional `link.name`. `link.mode` ist `catalog`, `sync` oder `connect` (nur seine Modelle und Agenten); `sync-e2e` aus älteren Antwortdateien ist `sync` mit jedem Workspace verschlüsselt. `link.sync.encryption` ist `all` (default und alle anderen Workspaces, auch später hinzugefügte) oder `off` (Standard; einzelne Workspaces werden später in den Einstellungen verschlüsselt). `link.use_models` und `link.use_agents` überschreiben, was der Modus vorgibt (an bei `connect`, sonst aus), und `link.catalog` fügt einem Synchronisierungsmodus den Katalog hinzu. Für die Synchronisierungsmodi nennt `link.sync.workspaces` die Workspaces hier (standardmäßig `["default"]`; ein fehlender wird angelegt) und `link.sync.spaces.<workspace>` den Bereich dort, dem jeder zugeordnet wird (standardmäßig für den ersten Ihr persönlicher Workspace dort, für die übrigen derselbe Name). Mit Verschlüsselung `link.sync.generate` (standardmäßig true) oder `link.sync.passphrase`, die Passphrase eines zweiten Geräts. Nimmt der andere Hub den Schlüssel nicht an, ist das in einer Antwortdatei eine Warnung, kein Abbruch: Die Verbindung wird gespeichert und beim Anwenden erneut versucht.

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
- **`ah` wird nicht gefunden.** Die venv (oder die conda-Umgebung, in die Sie installiert haben) liegt nicht im PATH, und der Shell-Hook ist nicht installiert. Führen Sie `ah shell-init --install` aus der aktivierten Umgebung aus oder rufen Sie das Skript mit seinem absoluten Pfad auf. Siehe [cli](cli.md). Auf den Wegen A und B hat der Host keinen `ah`, bis Sie einen hinzufügen, siehe [der Befehl `ah` auf jedem Weg](#der-befehl-ah-auf-jedem-weg).
- **Weg A: `unable to open database file`.** Der Schritt `init`, der das Volume an den Benutzer des Backends übergibt, ist nicht gelaufen. `docker compose up -d` führt ihn erneut aus (er ist idempotent), und `docker compose logs init` zeigt, warum er fehlgeschlagen ist.
- **Weg A: `.env` ist ein Verzeichnis.** Docker hat es angelegt, weil die Datei beim ersten Start des Stacks fehlte. Führen Sie `docker compose down` aus, dann `rmdir .env`, speichern Sie die Vorlage als `.env` und starten Sie noch einmal.
