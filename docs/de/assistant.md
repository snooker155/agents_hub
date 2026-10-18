# Assistent

Der Assistent ist ein Agent, über den eine Person den ganzen Dienst nutzt: fragen, was es Neues gibt, Aufgaben anlegen, Runs starten, die Ausgaben prüfen, einen Dienst verbinden, ohne die anderen Seiten zu öffnen. Es ist der System-Agent `assistant`, der den Main Agent erweitert (`extends`, [agent-inheritance](agent-inheritance.md)): Er hat die Werkzeuge und Anweisungen des Main Agent, dazu eigene Regeln für das Antworten und die Werkzeuge für den Zustand des Hubs. Ein Turn ist ein gewöhnlicher Chat-Turn: Werkzeugrichtlinien und Genehmigungen gelten. Die Stimme (Sprache hinein, Sprache heraus) baut auf demselben Turn auf, siehe [Stimme](#stimme).

## Die Seite

**Assistent** ist der erste Eintrag der Seitenleiste (`/assistant`). In der Mitte steht die Live-Marke, die zeigt, was gerade geschieht: Sie hört zu, während Sie sprechen, denkt nach, zeigt den Schritt, an dem ein Turn gerade ist, wartet auf eine Genehmigungskarte oder spricht. Ein Schritt spielt dieselbe Szene ab wie der Chat-Avatar und die Werkbank `/mark-lab`: Ein Nachschlagen richtet sich danach, was es liest (Runs und Sessions als Suche im Verlauf, Kosten und Datensätze als Datenbankabfrage, Modelle und Skills als Suche in den Werkzeugen, Benachrichtigungen als Suche in der Post). Das Schreiben einer Datei, das Delegieren und das Starten eines Flows oder eines Teams haben jeweils eine eigene Szene. Die Relay-Geschichten des Hubs und die Umbauten der Marke aus demselben Artefakt decken den Rest ab: ein Modell oder die Person fragen, eine Aufgabe weiterreichen, ein Deployment, Planen (ein Graph, der wächst und beschnitten wird), Zeitpläne (eine Uhr), Kalender, Summen (Zahnräder), ausgehende Nachrichten (eine Antenne), Synchronisieren, Importe, Schreibvorgänge in den Speicher und mehr. Ein Werkzeug, das keine Regel kennt, bekommt einen von wenigen Umbauten, die für keinen bestimmten Schritt stehen. Solange ein Werkzeug läuft, bleibt seine Szene sichtbar, auch wenn die Stimme sagt, was gerade geschieht. Darunter steht groß der erste Absatz der letzten Antwort und darunter die Sprechtaste.

- **Sprechen.** Unter der Taste stehen drei Arten zuzuhören (die Seite merkt sich die Wahl in diesem Browser):
  - *Gedrückt halten* (die Voreinstellung). Halten Sie die Taste gedrückt, oder halten Sie irgendwo auf der Seite außerhalb eines Textfelds die Leertaste, und sprechen Sie. Zum Senden lassen Sie los. Was verstanden wurde, wird in das Textfeld gesetzt, damit ein falsch verstandenes Wort korrigiert werden kann, und Enter sendet es als gesprochenen Turn. Eine Aufnahme endet nach einer Minute von selbst.
  - *Unterhaltung.* Tippen Sie auf die Taste (oder drücken Sie die Leertaste), um zu beginnen. Von da an bleibt das Mikrofon offen, und der Assistent wartet nach jeder eigenen Antwort ohne Taste auf Ihre. Eine Pause von etwa einer Sekunde beendet, was Sie sagen, und es wird sofort als gesprochener Turn gesendet. Sagen Sie "goodbye" ("пока", "tschüss", "конец разговора") oder tippen Sie auf die Taste, um die Unterhaltung zu beenden.
  - *Aktivierungswort.* Das Mikrofon bleibt offen, und der Assistent wartet auf seinen Namen, wie ein Telefon oder ein smarter Lautsprecher: "Assistent, was ist heute fehlgeschlagen?" ist ein Turn. "Assistent" allein spielt einen kurzen Signalton, und er hört einige Sekunden lang auf die Bitte. Nach einer Antwort hört er noch einige Sekunden auf eine Anschlussfrage ohne den Namen. Der Name muss das Gesagte eröffnen, ein Wort mitten im Satz ist also kein Ruf. Der Standardname ist "assistant" in jeder Sprache der Oberfläche ("ассистент", "Assistent"); in den Einstellungen lässt sich eine andere Wendung eintragen. Das funktioniert auf jeder Seite des Hubs, nicht nur hier: Auf einer anderen Seite zeigt eine Plakette am unteren Rand, dass das Mikrofon an ist (ihr × schaltet den Modus aus), und die Bitte öffnet den Assistenten mit ihr als erstem Turn.
- **Per Stimme stoppen.** Solange der Assistent arbeitet oder spricht, sagen Sie in jedem Modus "stop" ("стоп", "хватит", "hör auf", "Assistant, stop"), um den Turn und die Stimme zu stoppen, so wie es die Schaltfläche Stopp tut. Nur eine kurze Wendung zählt: "stop the nightly job" ist eine Bitte. Freihändig ist das Mikrofon ohnehin offen. Im Haltemodus wird es für die Dauer des Turns geöffnet, und erst, wenn das Mikrofon für die Seite erlaubt wurde, sodass eine getippte Frage nie die Abfrage des Browsers auslöst. *Per Stimme stoppen* in den Einstellungen schaltet es aus.
- **Zuhören.** Der erste Absatz der Antwort auf eine gesprochene Frage wird schon während des Streamens vorgelesen. War ein Turn eine Weile still, sagt der Hub, an welchem Schritt er ist. Eine wartende Karte wird in einem Satz angekündigt.
- **Eine Karte per Stimme beantworten.** Solange eine Karte wartet, halten Sie die Taste gedrückt und sagen Sie Ja oder Nein. In einer Unterhaltung oder im Aktivierungsmodus sagen Sie es einfach. "Stop" stoppt den ganzen Turn, statt die Karte abzulehnen. Eine Verbindungskarte wird nur am Bildschirm ausgefüllt.
- **Auf dem Bildschirm zeigen.** Ein Link in einer Antwort öffnet die Seite in einem Bereich neben dem Gespräch, mit dem Transkript als zweitem Reiter. **Diese Seite öffnen** verlässt dafür den Assistenten.
- **Einstellungen** (das Zahnrad): *Nur Sprache* sendet, was Sie sagen, sofort und liest jede Antwort vor, ohne Textfeld. *Ohne Ton* liest nie vor. *Stimme* wählt eine der Stimmen des Sprachmodells, das auf der [Seite Modelle](models.md#stimme) gewählt ist (dieselbe Liste, die der Reiter Stimme vorschlägt), und *Anhören* daneben spielt einen kurzen Satz mit ihr ab, in der Sprache der Stimme oder der der Seite (`POST /api/assistant/voice-sample`, abgerechnet als Run vom Typ `voice`). Dazu kommen *Per Stimme stoppen* (oben) und *Aktivierungswort*. Die Seite merkt sich die Einstellungen in diesem Browser.
- **Thread und Workspace.** Der nächste Turn läuft in dem Workspace, der in der Kopfzeile gewählt ist. Die Seite hat keine eigene Auswahl und nennt ihn nicht. Es gibt einen Thread und keinen Schalter.

Auf einem Telefon besteht die Seite aus der Marke, der Taste und der letzten Antwort. Links öffnen die Seite selbst.

**Auf der Chat-Seite.** Jede Unterhaltung mit dem Assistenten, gesprochen oder getippt, steht auch unter den Gesprächen der [Chat-Seite](chat.md) als Text, mit dem Etikett **Assistent**, gleich welcher Workspace gewählt ist: die aktuelle und die früheren, die **Neue Unterhaltung** abgelegt hat (bis zu 30). Eine gesprochene Zeile ist als *gesprochen* markiert. Beim Öffnen ist der Assistent gewählt, und eine dort getippte oder gesprochene Nachricht setzt die Unterhaltung fort: eine frühere wird zur aktuellen (`POST /api/entity-chats/sessions/activate`, die bisherige wird abgelegt), und der Zug geht an `POST /api/assistant` mit dem Workspace der Unterhaltung. Ist stattdessen ein anderer Agent gewählt, beginnt die Nachricht ein gewöhnliches Gespräch, das den Text der Unterhaltung mitnimmt. Die aktuelle hat **Auf der Assistenten-Seite öffnen**. `GET /api/chats` listet sie als `origin: "assistant"`, `read_only: false`, mit der ID `assistant~<thread>~<session>` und `assistant_thread` (`session_id`, `active`). `GET /api/chats/{id}` liefert das Transkript. Ein Schreib- oder Löschversuch über `/api/chats` ergibt 409 `read_only`, geleert wird auf der Assistenten-Seite. `GET /api/assistant/voice?workspace=` nennt die Transkriptions- und Sprachmodelle eines Zugs in diesem Workspace, ohne die aktuelle Unterhaltung zu verschieben; das Mikrofon der Chat-Seite fragt es.

Ohne Sprachmodelle funktioniert die Seite trotzdem: Ohne Transkriptionsmodell nutzt sie die eigene Spracherkennung des Browsers (die Seite weist darauf hin, dass das Audio dann an den Hersteller des Browsers geht), und ohne Sprachmodell die eigene Stimme des Browsers. Hat der Browser keines von beiden, ist es ein Text-Chat. Die [Demo](demo.md) läuft auf diese Weise.

**Wie das offene Mikrofon hört.** Mit einem Transkriptionsmodell schneidet die Seite das Audio selbst in Sprechabschnitte (ein Pegel über dem Grundrauschen des Raums; ein Abschnitt endet nach einer Pause) und sendet jeden als WAV an `/transcribe`. Solange der Assistent arbeitet oder spricht, wird nur ein kurzer Abschnitt gesendet (ein Stopp ist kurz; ein längerer wird verworfen, ohne transkribiert zu werden), und die Stimme muss lauter sein als üblich, damit seine eigene Stimme aus den Lautsprechern selten für Ihre gehalten wird: Kopfhörer helfen. Im Aktivierungsmodus wird jede kurze Wendung, die in der Nähe des Mikrofons fällt, transkribiert und berechnet, und die Seite sagt das. Ohne Transkriptionsmodell hört die eigene Erkennung des Browsers durchgehend zu, und das Audio geht an deren Hersteller. Alles Gehörte wird in der Seite auf die ganze Wendung geprüft: ein Stopp, das Ende einer Unterhaltung, der Name.

## Ein Thread

`GET`, `DELETE` und `POST /api/assistant` sowie `POST /api/assistant/stop`: das Transkript, ein frischer Thread, ein Turn (ein Server-Sent-Event-Stream wie in jedem Chat) und das Stoppen eines laufenden Turns. Der Thread folgt Ihnen über Seiten hinweg.

Die Session des Threads und Ihr Speicher liegen im **Heimat-Workspace**, das ist `default`. Der persönliche Speicher ist für den Assistenten in jedem Workspace standardmäßig eingeschaltet, wie beim Main Agent, und der Assistent nutzt immer den Pool des Heimat-Workspace, wo auch immer ein Turn läuft.

**Der Thread ist das Kurzzeitgedächtnis.** Jeder Turn des aktuellen Gesprächs erreicht das Modell, nicht nur die letzten. Sobald die Turns nach der Zusammenfassung der Session etwa 24.000 Zeichen überschreiten, verdichtet die Antwort, die die Grenze überschritten hat, die älteren in die Zusammenfassung (denselben Datensatz, den die [Verdichtung der Chat-Seite](chat.md#compaction) an der Session führt) und lässt etwa 10.000 Zeichen wörtlich stehen. Die Verdichtung läuft nach der Antwort, eine gesprochene Antwort wartet also nie darauf. Geschrieben wird sie von dem Modell, auf dem der Turn lief, und bei einem Fehlschlag dieses Aufrufs tritt ein grober Auszug an ihre Stelle. Der nächste Prompt besteht aus der Zusammenfassung und jedem Turn seitdem. Andere Seiten-Chats behalten nur ihre letzten zwölf Nachrichten, weil der Datensatz, um den es dort geht, dem Modell in jedem Turn vollständig gezeigt wird.

Der Rumpf eines Turns: `message`, `workspace` (wo der Turn läuft; leer ist die Heimat), `references` (Datensätze des Hubs zum Anhängen, wie im Seiten-Chat; ein Datensatz eines anderen Workspace wird verworfen) und `voice` (ob die Nachricht gesprochen wurde). Die Antwort auf `GET` ergänzt `home` und `workspaces` (wo ein Turn laufen kann).

## Frühere Gespräche

Eine neue Unterhaltung (`DELETE /api/assistant`, die Schaltfläche neben den Einstellungen) legt die laufende unter den früheren ab. **Gespräche**, der Reiter neben dem Transkript, listet sie auf. Wählen Sie eines aus, wird es zur aktuellen Unterhaltung, und die nächste Nachricht setzt es unter der Gesprächs-ID fort, die es schon in Läufe hatte. **Verlauf leeren** (der Radiergummi über dem Transkript, `DELETE /api/assistant/conversation`) verwirft stattdessen die laufende Unterhaltung, ohne sie abzulegen. Ihre Runs bleiben auf der Seite Läufe und in den Kosten. Beides wird mit 409 `busy` abgelehnt, solange ein Turn läuft.

Auch der Assistent kommt mit `assistant_conversations` heran: "Worüber haben wir gesprochen" listet die letzten zehn auf, die neueste zuerst, aus dem Workspace, in dem der Turn läuft (jede Nachricht trägt den Workspace, in dem ihr Turn lief; "in jedem Workspace" listet alle), "die nächsten zehn" blättert weiter, und "geh zurück zum Gespräch über X" findet es und öffnet es. Ein Turn kann den Thread, in den er schreibt, nicht austauschen, der Wechsel geschieht deshalb am Ende des Turns: Der Stream endet mit `{"type": "conversation", "session_id"}`, und die Seite lädt den Thread neu. Eine Unterhaltung, die nur aus dieser Bitte bestand, wird nicht behalten. Das Werkzeug liest nur den eigenen Thread des laufenden Turns und liefert Titel und die eigenen Nachrichten, nie eine Antwort.

## Wo ein Turn läuft

Jeder Turn läuft in einem Workspace: Sein Run wird dort abgelegt, seine Werkzeuge sind dort festgelegt wie bei jedem Agenten ([workspaces](workspaces.md)), und Dateien, die er schreibt, landen dort. Der Workspace muss existieren (sonst 404). "Wechsle zum Vertriebs-Workspace" ist der nächste Turn, der mit `workspace: "sales"` gesendet wird.

Der Prompt des Turns sagt dem Agenten, wo der Turn läuft, was der Heimat-Workspace ist, welche Workspaces er erreichen kann, ob die Nachricht getippt oder gesprochen wurde und wie der Zustand des Hubs in diesem Workspace aussieht (dieselbe Momentaufnahme, die das [Help-Panel](help.md) liest).

## Der Dienst-Thread

Nur wenn der Turn in `default` läuft, hält der Assistent die Dienstwerkzeuge (`common/workspace_scope.py` `ASSISTANT_SERVICE_TOOLS`: `service_health`, `run_diagnostics`, `service_lookup`, `list_sessions`, `routing_log`, `list_instances`, `list_containers` sowie das Stoppen oder Neustarten eines Runs, einer Instanz oder eines Containers) und die Werkzeuge zur Verwaltung von Workspaces. Logs von Runs, Fehlern und Containern gehören nicht dazu: Sie enthalten Text, den jeder hätte schreiben können, der Assistent kann auch Nachrichten senden, und diese Kombination lehnt der Capability Guard ab. Logs liest der Service Agent.

## Was er nachschlagen kann

Fragen zu jeder Seite beantwortet der Assistent mit einem einzigen schreibgeschützten Werkzeug, `hub_lookup` (`chat/lookup.py`), statt mit einem Werkzeug pro Seite. Ein Nachschlagen nennt eine Art (kind) und listet entweder auf (mit optionalem Suchtext) oder beschreibt einen Datensatz anhand seiner ID. Jede Zeile und jede Karte trägt `url`, die Seite, die den Datensatz zeigt und die der Assistent als "auf dem Bildschirm zeigen" verlinkt.

| Art | Listet | Ein Datensatz (`id`) | Seite |
|---|---|---|---|
| `run` (Alias `message`) | Runs, neueste zuerst, nach Titel, Agent oder Status | Status, Zeitverlauf, Modell, Tokens, Kosten, Werkzeugaufrufe, die erste Zeile des Fehlers | `/messages/<id>` |
| `session` | Sessions | Agent, Daten, Anzahl der Runs, die letzten | `/sessions/<id>` |
| `model` | aktivierte Modelle mit Preisen und die Stimmmodelle | Preise, Kontextfenster, ob es hier das Standardmodell ist, die Stimmmodelle | `/models/<provider>/<model>` |
| `voice` (`speech`, `transcription`) | die Transkriptions- und Sprachmodelle, die die Assistenten-Seite nutzt, oder die des Browsers, wenn es keine gibt | das Modell, die Sprachstimme | `/models?tab=voice` |
| `agent` | Agenten des Workspace | Beschreibung, Werkzeuge, an wen er delegiert, was er erweitert | `/agents/<id>` |
| `notification` | ungelesene Benachrichtigungen | Titel, Text, Schweregrad | |
| `approval` | Werkzeugaufrufe und Aufgaben, die auf Ihre Genehmigung warten | Werkzeug, Grund, Run, Ablauf | der Run oder die Aufgabe |
| `task`, `view`, `project`, `loop`, `flow`, `team`, `job` | so, wie die Referenzauswahl des Chats sie listet | so, wie die Auswahl sie darstellt | ihre Seite |
| `instance` | residente Kopien und Aufgabenkopien von Agenten | Status, Agent, Runs, die erste Zeile des Fehlers | `/instances/<id>` |
| `guardrail` | Guardrails | Stufe, Art, Aktion, Anzahl der Ereignisse je Aktion, nie der gefundene Text | `/guardrails` |
| `tool` | der Werkzeugkatalog | Fähigkeiten, Genehmigung, der Modus hier, welche Agenten es halten, jüngste Entscheidungen | `/tools` |
| `skill` | Skills | Prüfstatus, Version, Anzahl der Schritte, nie die Schritte | `/skills` |
| `mcp` | MCP-Server (ID `workspace/server`) | Transport, aktiviert, Anzahl der Werkzeuge, Genehmigung, ob ein Fehler vorliegt | `/mcp` |

`workspace` ist, wenn leer, der Workspace des Turns, ein beliebiger Workspace oder `all`. Ein anderer Agent, dem `hub_lookup` gewährt wurde, liest nur seinen eigenen Workspace.

Es liefert Metadaten, nie die Antwort eines Runs, ein Log oder die Nachrichten eines Chats: Diese enthalten, was auch immer der Run verarbeitet hat, Webseiten eingeschlossen, und ein Agent, der solchen Text liest und zugleich Nachrichten senden kann, ist genau das, was der Capability Guard ablehnt ([tool policy](tool-policy.md)). Die Antwort ist auf der verlinkten Seite einen Klick entfernt. Jede Seite des Dashboards wird von einer Art oder von einem der eigenen Werkzeuge des Assistenten beantwortet. `tests/test_hub_action.py` schlägt fehl, wenn eine neue Seite ohne eines von beiden hinzukommt.

Text, den ein Fremder geschrieben haben könnte, bleibt draußen: Eine Webadresse wird auf ihren Host reduziert, der Fehler eines entfernten Servers auf "hat einen Fehler", und der gefundene Text eines Guardrails wird nie gelesen.

### Dienstweite Datensätze

Im Dienst-Thread hält der Assistent außerdem `service_lookup`, denselben Katalog für die Datensätze, die zu keinem Workspace gehören. Das Nachschlagen in einem Workspace-Thread lehnt diese Arten ab (`service_only`).

| Art | Listet | Ein Datensatz (`id`) | Seite |
|---|---|---|---|
| `health` | die Momentaufnahme und die Prüfungen des Doctors | Status, Zusammenfassung und Dokumentationsabschnitt einer Prüfung (`snapshot` für das Ganze) | `/health` |
| `container` | verwaltete Container | Image, Status, Agent, Host | `/containers` |
| `web_log` | was `fetch_url` gelesen hat | Host, Werkzeug, Status, Schweregrad, Anzahl der Flags | `/web-logs` |
| `setting` | Anbieter, rag, web, execution | einfache Einstellungen; Geheimnisse nur als gesetzt oder nicht, URLs ohne Zugangsdaten | `/settings` |

Die Prüfung des Doctors, die über das Netzwerk nach außen ruft (Anbieter), wird von einem Nachschlagen nicht ausgeführt. Ihre Karte verweist auf die Seite Status.

## Was er ändern kann

Eine Wendung wie "jeden Morgen um 8 sag mir das Wetter" wird zu einem Job auf der Plan-Seite: Nach dem Ja der Person ruft der Assistent `schedule_task` auf (siehe [scheduling](scheduling.md)).

`hub_action` tut eine kleine Sache mit einem Datensatz: eine Art, ein Verb und eine ID. Bauen, Bearbeiten und Löschen bleiben bei den Seiten und den Creator-Agenten, und nichts hier startet kostenpflichtige Arbeit.

| Art | Aktionen |
|---|---|
| `instance` | `stop` (der Prozess einer residenten Kopie oder der aktuelle Run einer anderen Kopie), `restart` (eine residente Kopie) |
| `guardrail` | `enable`, `disable` |
| `mcp` | `enable`, `disable` |

Jeder Aufruf wartet auf ein Ja: `hub_action` fragt bei jedem Aufruf, was auch immer die Werkzeugrichtlinie des Workspace sagt, wie `delete_workspace`. Die Karte sagt in einem Satz, was geschehen wird ("Instanz Inbox helper in team stoppen: Ihr aktueller Lauf endet."). Sie beantworten sie am Bildschirm oder mit einem kurzen gesprochenen Ja. Wo niemand vor einer Karte sitzt, wird der Aufruf abgelehnt.

## Geführte Einrichtung

Nach der Installation hat der Hub ein Modell. Den Rest des Weges führt der Assistent die Person ([installation](installation.md#after-the-install-the-assistant-takes-over)). Das Willkommensfenster oder der letzte Bildschirm der Ersteinrichtung startet ihn mit **Mit dem Assistenten sprechen** (die Antworten werden vorgelesen, und die Seite hört zu, solange die Taste oder die Leertaste gehalten wird, der Modus Gedrückt halten) oder **Dem Assistenten schreiben**. Die Plakette **Einrichtung** in der Kopfzeile und `/assistant?setup=1` führen zu ihm zurück, und der Reiter **Einrichtung** der Seite zeigt jeden Schritt. Ein Bildschirm der Ersteinrichtung, der mit „Nicht jetzt“ beantwortet wurde (Stimme, Websuche, Demo), beginnt hier übersprungen.

Die Anleitung (`common/setup_guide.py`, `GET /api/setup-guide`) ist eine Liste von Schritten, jeder mit dem Grund, warum er wichtig ist, der Art, wie sein Abschluss erkannt wird, dem, was der Assistent dafür tut, und der Seite, die ihn zeigt:

| Schritt | Erledigt, wenn | Der Assistent |
|---|---|---|
| `model` | ein Modellanbieter einen Schlüssel hat oder ein eigenes Backend mit einem Modell | `propose_connection` kind `provider` |
| `default_model` | der Standardanbieter laufen kann und ein Modell hat | `setup_step` `choose_model` (ausgewogen empfohlen) |
| `voice` | `default` Sprach- und Transkriptionsmodelle hat | `setup_step` `voice_cloud` |
| `demo` | der Demo-Workspace vorhanden ist | `setup_step` `seed_demo` |
| `health` | vom Assistenten nach `run_diagnostics` als erledigt markiert | `run_diagnostics` |
| `first_chat` | ein Chat existiert | die Chat-Seite |
| `accounts` | ein Jira- oder GitLab-Konnektor konfiguriert ist | `propose_connection` kind `connector` |
| `first_agent` | ein eigener Agent der Person existiert | `agent_creator`, der Marktplatz |
| `first_task` | eine Aufgabe existiert | `create_task` |
| `automation` | ein geplanter Job existiert | verweist auf `/plan`: Ein Zeitplan dort startet einen Agenten, einen Flow oder einen Loop zu den Zeiten, die Sie wählen |
| `tour` | der Browser meldet, dass die Willkommenstour gemacht wurde | die Schaltfläche für die Tour im Reiter Einrichtung |

Die ersten sechs gehören zur Einrichtung der Installation. Ob ein Schritt erledigt ist, wird bei jedem Aufruf aus dem Hub gelesen, ein Schritt, der auf seiner Seite erledigt wurde, wird in der Anleitung also ebenfalls abgehakt, und die Agenten, Chats und Aufgaben der Demo haken nichts ab. Gemerkt werden nur Überspringen, als erledigt markierte Schritte und ob die Anleitung läuft. **Übersprungene Schritte zurückholen** im Reiter Einrichtung (sichtbar, sobald etwas übersprungen oder markiert ist) hebt beides auf und lässt die Anleitung wieder ab dem ersten offenen Schritt laufen. Die Ersteinrichtung (Einstellungen, Ersteinrichtung) und die Tour wiederholt es nicht.

**Einrichtung schließen** unten im Reiter Einrichtung und das Kreuz an der Plakette in der Kopfzeile beenden die Anleitung, solange noch Schritte offen sind; sind alle Schritte erledigt oder übersprungen, steht dort stattdessen **Abschließen**. Bevor die Pflichtschritte (ein Modell, das Standardmodell) erledigt sind, erscheint keins von beiden, und `setup_guide` lehnt `finish` und `dismiss` ab. Der Assistent startet die Anleitung wieder, sobald die Person darum bittet.

Solange die Anleitung läuft, trägt jeder Turn sie mit sich ("Guided setup" im Prompt): die Schritte mit ihrem Status, den nächsten, warum und wie. Der Assistent macht pro Antwort einen Schritt, öffnet seine Seite mit `show_on_screen` neben dem Gespräch und liest oder markiert die Anleitung mit `setup_guide` (`status`, `options` für die Modellstufen, Stimmen und Suchanbieter, `skip`, `done`, `finish`).

`setup_step` nimmt eine Änderung an der Installation vor und wartet wie `hub_action` bei jedem Aufruf auf ein Ja auf einer Karte, die sagt, was sich ändert ("Make openai/gpt-5.4 the hub's default model..."). Es macht eines von diesen:

- `choose_model`: markiert das Modell im Katalog von [Modelle](models.md) mit einem Stern und aktiviert es, und schreibt `DEFAULT_PROVIDER` sowie das Modell des Anbieters in die `.env`, so wie es `ah setup` tut.
- `voice_cloud`: Sprach- und Transkriptionsmodelle von OpenAI oder Google mit einer Stimme, als Stimmmodelle des Hubs (Seite Modelle, Reiter Stimme).
- `seed_demo`: der [Demo-Workspace](demo.md).

Ein Schlüssel ist nie ein `setup_step`: `propose_connection` mit kind `provider` (Ziele `openai`, `anthropic`, `google`, `brave`, `tavily`, `exa`) zeigt eine Karte, in die die Person den Schlüssel tippt. Ein Modellschlüssel wird gegen die Modellliste des Anbieters geprüft, bevor etwas gespeichert wird. Ein abgelehnter Schlüssel lässt die Karte offen.

Ein Schlüssel, der über eine Karte, das Willkommensfenster oder die Seite Einstellungen gespeichert wird, gilt ohne Neustart für den nächsten Modellaufruf: Er wird in die Umgebung des Backends kopiert und an jeden danach gestarteten Prozess weitergegeben (`common/provider_env.py`).

## Antworten

Der Assistent eröffnet jede Antwort mit ein oder zwei Sätzen, die sich vorgelesen gut anhören, und gibt dann Details und Links zu der Seite, auf der die Person die Sache sehen kann. Vor allem, was Geld kostet, nennt er die Kosten und stellt eine Ja-Nein-Frage. Nach einem Geheimnis fragt er nie: Eine Verbindung läuft über `propose_connection`, dessen Karte das Geheimnis einsammelt.

Genehmigungskarten und Verbindungskarten erreichen den Stream des Threads wie auf der Chat-Seite (`tool_approval`-Events).

## Stimme

Die Stimme ist ein Weg hinein und ein Weg hinaus desselben Turns, keine zweite Schleife: kein Speech-to-Speech-Modell, damit Richtlinien und Genehmigungen auf dem Weg bleiben. Beide Richtungen nutzen die Stimmmodelle des Hubs: ein Sprach- und ein Transkriptionsmodell, gewählt auf der [Seite Modelle](models.md#stimme), Reiter Stimme, in welchem Workspace der Turn auch läuft. Sie werden vom Hub direkt aufgerufen, nicht als Agentenwerkzeuge: Ob gesprochen wird, entscheidet die Person, nicht das Modell.

**Sprache hinein.** `POST /api/assistant/transcribe` mit der Aufnahme als Rumpf und ihrem `Content-Type` (`audio/webm`, `audio/ogg`, `audio/wav`, `audio/mp4` oder `audio/mpeg`), `?language=` (`en`, `ru`, `de`). `?purpose=wake` oder `?purpose=monitor` (das offene Mikrofon, das auf den Namen oder auf einen Stopp lauscht) benennt nur den Kosten-Run: "Voice input (wake phrase)", "Voice input (stop)". Die Antwort ist `{text, language, run_id, cost_usd, consent}`. Die Seite zeigt den Text, die Person kann ihn korrigieren, und er wird als gewöhnlicher Turn mit `voice: true` gesendet. Zwei Anfragen statt einer, damit ein falsch verstandenes Wort korrigiert wird, bevor es zur Anweisung wird. Die Aufnahme wird nicht aufbewahrt, nur ihr Preis, als eigener Run (Kanal `voice`, Titel "Voice input") im Workspace, in dem der Turn laufen wird. Höchstens 8 MB (`AGENTS_HUB_VOICE_MAX_BYTES`) und, bei WAV, 120 Sekunden (`AGENTS_HUB_VOICE_MAX_SECONDS`).

**Sprache heraus.** Der Stream jedes Turns beginnt mit einem Event `run`, das die Run-ID trägt. Die Seite liest den ersten Absatz der Antwort satzweise vor, während die Tokens eintreffen, mit `POST /api/assistant/speak` und `{run_id, text}` (höchstens 1000 Zeichen). Die Antwort ist Audio (`audio/mpeg` oder `audio/wav`, je nachdem, was das Modell liefert). Gelesen werden nur die eigenen Worte des Turns: Text, der nicht in dem steht, was der Turn gestreamt hat, oder in seiner Antwort im Datensatz, wird mit 403 `not_in_answer` abgelehnt, und ein Turn, dessen Antwort noch nicht aufgezeichnet ist, antwortet mit 409 `not_ready`. Markdown wird zuvor bereinigt: Ein Link wird nach seinem Ankertext gelesen, Hervorhebungen, Überschriften und Aufzählungszeichen entfallen, und Code und Tabellen werden durch "Details stehen auf dem Bildschirm" ersetzt. Der Preis wird dem Run des Turns zugeschlagen (`voice_calls`). 204, wenn nichts zum Vorlesen übrig bleibt.

Der Hub spricht auch von sich aus, in der Sprache der Seite:

- `{run_id, approval_id}`: ein Satz für eine Karte, die im Turn wartet, mit dem, was der Aufruf tut, und seinen Kosten, wenn die Karte welche nennt. Bei einer Verbindungskarte: dass die Angaben in die Karte auf dem Bildschirm gehören.
- `{run_id, tool, agent}`: der Schritt, an dem ein langer Turn gerade ist ("ich gebe das an den Researcher weiter"), aus dem Event `tool_start` des Streams.

**Ein gesprochenes Ja.** Wartet im Thread eine Karte, beantwortet ein Turn, der mit `voice: true` gesendet wird und dessen Text ein kurzes Ja oder Nein ist (bis zu vier Wörter, auf Englisch, Russisch oder Deutsch), die Karte, statt einen Turn zu starten. Der Stream trägt dann `{"type": "voice_answer", "approval_id", "decision", "status"}`, und die Antwort wird mit `"via": "voice"` festgehalten. Alles Längere, alles Getippte und ein Ja ohne wartende Karte ist eine gewöhnliche Nachricht. Eine Verbindungskarte wird nie per Stimme beantwortet (`status: "on_screen"`), und nichts aus einem Transkript wird je in sie geschrieben. Warten mehrere Karten, lautet die Antwort `status: "ambiguous"`, und sie werden am Bildschirm beantwortet.

Fehler, die die Seite behandelt: 409 `model_not_added` (kein Transkriptions- oder Sprachmodell; die Seite bietet stattdessen die Erkennung oder Stimme des Browsers an, mit dem Hinweis, dass das Audio dann an den Hersteller des Browsers geht), 413 `too_long`, 415 `unsupported_audio`, 502 `provider_error`.

### Eine Stimme pro Sprache

Eine Cloud-Stimme spricht alle Sprachen, doch zu einer Sprache passt oft eine
Stimme besser als eine andere. Deshalb kann das Sprachmodell pro Sprachcode
eine Stimme und, wo der Anbieter es erlaubt, ein Modell nennen (`languages`
im Sprachmodell, die Zeilen **Eine Stimme pro Sprache** auf der Seite
Modelle, Reiter Stimme):

```json
{"provider": "openai", "model": "gpt-4o-mini-tts", "voice": "alloy",
 "languages": {"ru": {"voice": "coral"},
               "de": {"voice": "onyx"}}}
```

Die Sprache einer Antwort wird aus dem Text selbst bestimmt (zuerst die
Schrift, dann häufige Wörter, sonst die Sprache der Seite), und Modell und
Stimme dieser Sprache lesen sie; eine Sprache ohne eigene Zeile liest das
Modell des Eintrags. Eine Antwort behält eine Stimme: die Sprache wird einmal
bestimmt, aus dem, was der Zug bis zum ersten gesprochenen Satz gesagt hat,
also liest eine russische Antwort mit englischem Zitat durchgehend die
russische Stimme. Das gilt für `/speak` und für eine Stimmprobe in
einer Sprache; die Stimme des Browsers wählt ihr Gebietsschema genauso.

## Limits

In einem Thread läuft jeweils ein Turn: Ein Senden, während einer läuft, ergibt 409 `busy` (außer einem gesprochenen Ja oder Nein für eine wartende Karte, siehe oben). Jeder Turn ist ein Run, er erscheint also in Läufe und zählt in den Kosten.
