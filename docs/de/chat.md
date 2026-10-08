# Chat

Die Eingangstür. Sie sprechen direkt mit jedem Agenten im Workspace: kein Knoten, keine Aufgabe, keine Einrichtung.

## Was Sie ansprechen können

- **Einen Agenten**: ein Agent antwortet.
- **Einen Flow**: der ganze Graph läuft, mit Streaming pro Knoten.
- **Ein Team**: die Besetzung bearbeitet die Anfrage über ihr gemeinsames Board.

Die Auswahl steht standardmäßig auf dem Standard-Chat-Agenten des Workspace. Das ist der Hauptagent, solange Sie ihn nicht geändert haben.

## Wo ein Turn läuft

Das Backend führt den Agenten nicht selbst aus. Ein Turn wird an ein Replikat eines [Dienstes](services.md) übergeben: an den eigenen Dienst des Agenten im Workspace, falls er einen hat, sonst an den Runner des Workspace, einen Prozess, der genau dafür bereitgehalten wird. Das Replikat führt die ganze Pipeline aus (Prompt, Anhänge, Verdichtung, Werkzeuge, Handoffs, den Run-Datensatz) und streamt seine Events zurück. Die Seite sieht, was sie immer gesehen hat. Der erste Turn in einem Workspace ohne Runner wartet, bis einer gestartet ist, höchstens `AGENTS_HUB_TURN_START_TIMEOUT` Sekunden. Danach bleibt der Runner aktiv (`AGENTS_HUB_RUNNER_MIN`, Standard 1). Mit `AGENTS_HUB_CHAT_EXECUTION=inprocess` (Einstellungen, "Chat-Ausführung") laufen die Turns stattdessen im Backend.

## Was der Agent sieht

Das bisherige Gespräch und seinen eigenen Systemprompt. Er sieht **nicht** andere Gespräche, andere Workspaces oder das, was Sie einem anderen Agenten gesagt haben.

Der Verlauf wird als Nachrichten übertragen, eine pro Turn, nicht als Transkript, das vor Ihre neue Nachricht gesetzt wird. Das Modell liest ihn als Gespräch, und der Teil des Prompts, der sich von Turn zu Turn nicht ändert, bleibt identisch. Genau das macht die unten beschriebenen Anbieter-Caches möglich. Gesendet werden die letzten 40 Turns, jeder auf 4.000 Zeichen begrenzt, insgesamt höchstens 60.000 Zeichen. Was älter ist, entfällt oder wird zu einer Zusammenfassung verdichtet, sobald die Verdichtung greift.

Anhänge, referenzierte Entitäten und der Hinweis auf den Projektbereich gehören zu dem Turn, der sie mitgebracht hat. Sie stehen deshalb in der Nachricht dieses Turns und nicht im Systemprompt.

## Verdichtung

Ein langes Gespräch passt irgendwann nicht mehr in das Modell. Statt den Turn scheitern zu lassen, wird der ältere Teil zu einer einzigen Zusammenfassung verdichtet, und nur der jüngste Abschnitt wird wörtlich gesendet.

- **Wann.** Sobald das Gespräch samt Systemprompt 60 Prozent des Kontextfensters des Modells überschreitet. Ist das Fenster unbekannt (ein lokales Modell, ein Gateway, das nichts meldet), liegt die Schwelle bei 60.000 Zeichen. Die Grenze von 60.000 Zeichen für den Verlauf selbst ist bei einem Modell mit großem Fenster zuerst erreicht. In der Praxis wird deshalb bei kleinem Fenster verdichtet, hinter einem langen Systemprompt oder wenn ein Anbieter den Turn ablehnt.
- **Was erhalten bleibt.** Die jüngsten Turns, immer mindestens vier, werden wörtlich gesendet. Alles davor wird zur Zusammenfassung.
- **Wer sie schreibt.** Derselbe Anbieter und dasselbe Modell, auf dem der Agent läuft, in einem Aufruf ohne Werkzeuge und mit einer Antwort von höchstens etwa 800 Tokens. Schlägt dieser Aufruf fehl, tritt ein verlustbehafteter Ersatz an seine Stelle: die erste Anfrage, die letzten paar verdichteten Turns und eine Markierung, wie viele Nachrichten ohne Zusammenfassung entfallen sind. Der Agent kann dann nach der Lücke fragen, statt sie zu erfinden.
- **Wo sie liegt.** In der Session, zusammen mit der Anzahl der Nachrichten, für die sie steht, und einem Fingerabdruck der letzten davon. So wird sie auch dann an der richtigen Stelle gefunden, wenn das Gesprächsfenster weitergerückt ist. Der nächste Turn liest sie zurück, statt dasselbe Material noch einmal bezahlt zusammenzufassen, und eine spätere Verdichtung erweitert dieselbe Zusammenfassung um alles, was seitdem aus dem jüngsten Abschnitt herausgefallen ist.
- **Bei einem Überlauf.** Lehnt der Anbieter den Turn trotzdem ab, wird der Verlauf verdichtet und der Turn einmal wiederholt. Erst danach erreicht Sie der Fehler "context is full".

Der Turn, der ein Gespräch verdichtet, sendet im Stream ein Event `compaction`, mit der Anzahl der verdichteten Nachrichten und der Länge der Zusammenfassung.

## Prompt-Caching

Anbieter berechnen für ein Prompt-Präfix, das sie schon haben, nur einen Bruchteil des Eingabepreises. Zwei Dinge sorgen hier dafür.

- **Anthropic** speichert nur zwischen, was markiert ist. Deshalb wird der Systemprompt als Block mit `cache_control: ephemeral` gesendet, und die Session-Zusammenfassung wird dahinter markiert. Ein Systemprompt unter 4.000 Zeichen (etwa 1.024 Tokens) bleibt unmarkiert: Anthropic ignoriert einen so kleinen cachefähigen Block, die Markierung würde also einen Cache-Schreibvorgang bezahlen, auf den kein Lesen folgt.
- **OpenAI** speichert stabile Präfixe von selbst zwischen, ohne dass etwas markiert werden muss. Dafür muss das Präfix von Aufruf zu Aufruf byte-identisch sein. Das erreicht der Versand des Verlaufs als Nachrichten: Vor dem Gespräch wird nichts Turn-spezifisches eingefügt.

Alle anderen Anbieter bleiben unberührt und senden genau das, was sie vorher gesendet haben.

Die Cache-Treffer kommen in der Token-Nutzung des Runs zurück und werden getrennt von frischer Eingabe gemeldet. Die Seite [costs](costs.md) bewertet sie mit dem Cache-Preis des Modells. Ein langes Gespräch kostet also das, was der Anbieter tatsächlich berechnet hat, und nicht das, was es ganz ohne Cache gekostet hätte.

## Anhänge und Verweise

Dateien lassen sich anhängen und Entitäten des Workspace referenzieren, damit der Agent mit der Sache selbst arbeitet und nicht mit Ihrer Beschreibung davon.

Eine Datei, die schon im Workspace liegt, wird über ihre ID angehängt ("Aus den Dateien des Arbeitsbereichs" im Anhängen-Menü). Der Server lädt sie, und sie muss zum Workspace des Chats gehören. Wenn Sie bei einem Upload "Im Workspace speichern" ankreuzen, wird die Datei sofort als Workspace-Datei abgelegt. Spätere Turns, Aufgaben und Speicherpools können sie dann wiederverwenden. Siehe [workspace files](files.md).

Antwortet der Agent aus den Dokumenten eines Speicherpools, listet die Antwort ihre Quellen auf, und jedes `[n]` im Text verweist auf eine davon. Das Event `done` enthält sie als `citations`. Siehe [workspace files](files.md#citations).

## Slash-Befehle

`/help`, `/clear`, `/new` und `/config` werden in der Seite selbst verarbeitet. Ein Agent kann auch eigene Befehle definieren, die in derselben Auswahl erscheinen.

## Bereiche neben dem Gespräch

Rechts in der oberen Leiste sitzen drei Schalter. In der Chat-Ansicht steht zuerst die Schaltfläche **Prozess**: der Graph des Runs, die Werkzeuge und der Token-Verbrauch. **Chat** oder **Build** wählt die Ansicht: nur die Nachrichten oder das ganze Transkript mit Denken, Plan und Werkzeugaufrufen im Text. Die Build-Ansicht zeigt den Run schon, deshalb hat sie keine Schaltfläche Prozess. **Artefakte** und **Code** öffnen die Spalte neben dem Transkript, jeweils nur eine (das Öffnen der einen schließt die andere), und zeigen, wie viele das Gespräch hervorgebracht hat: Dateien, die die Runs geändert haben, und Ansichten, die sie erstellt haben, unter Artefakte, Code-Ausschnitte unter Code. Die Spalte Artefakte listet links die Dateien und Ansichten auf und zeigt rechts die ausgewählte. Der Schalter in ihrer Kopfzeile wählt **Dateien** (die Voreinstellung: jede Datei im heutigen Stand, aus dem Ordner des Workspace gelesen, eine gelöschte Datei erscheint also nicht) oder **Diff** (was die Runs geändert haben, Löschungen eingeschlossen). Eine Ansicht wird in beiden Fällen vollständig dargestellt. Der Prozess ist eine eigene Spalte, rechts vom geöffneten Bereich. Jeder Bereich merkt sich, ob er geöffnet war.

## Code-Panel

Liefert ein Agent einen ausführbaren oder bearbeitbaren Ausschnitt zurück (eine `code`-Ansicht, siehe [views](views.md#code)), öffnet er sich in einem eigenen Panel, statt in einem Codeblock zu stehen: ein Editor, dazu Kopieren, Herunterladen, Ausführen, In Projekt speichern, Besprechen und Bearbeiten. Das Panel listet jeden Code-Ausschnitt der Session auf, sodass Sie zwischen mehreren wechseln können, ohne Ihre Stelle zu verlieren. Darunter listet "Aus Antworten" die Codeblöcke der Antworten dieses Gesprächs auf. Sie heißen nach der Datei, die die Antwort genannt hat, sonst nach der ersten Klasse oder Funktion im Code, sonst nach dem Prompt, auf den sie antworten. Öffnen Sie dort einen Block (oder "Im Code-Panel öffnen" am Block selbst), erscheint er im Editor mit denselben Aktionen wie eine Code-Ansicht: Er läuft so, wie er ist. "Version speichern" führt einen eigenen Verlauf (der Text der Antwort ist Version 1) mit einem Diff zwischen beliebigen zwei Versionen, und "In Projekt speichern" schreibt ihn als Datei in den Ordner eines Projekts. Nichts davon macht daraus eine Ansicht. Das tut "Als View speichern", auf Wunsch: Der Block wird zu einer Code-Ansicht des Gesprächs, die bei den Ansichten aufgelistet wird und einen eigenen Run-Verlauf hat, und er verschwindet aus "Aus Antworten". Die Zahl an der Schaltfläche Code schließt die Blöcke ein. Das Bearbeiten und das Ausführen einer Ansicht erzeugen jeweils eine neue Version. "Besprechen" gibt den Ausschnitt samt Text an den Agenten zurück. "Bearbeiten" beginnt nur eine Anfrage dazu, nutzen Sie es also, solange der Ausschnitt noch frisch im Gespräch ist.

## Delegation aus dem Chat

`run_agent_tool` ist Delegation ohne Aufgabe und funktioniert nur hier, nicht innerhalb einer nachverfolgten Aufgabe. Das Kind läuft bis zum Ende, und seine Ausgabe kommt im Werkzeugergebnis zurück. Der Agent, mit dem Sie sprechen, kann sie also verwenden und Ihnen antworten.

## Handoffs

Ein Agent kann das Gespräch auch abgeben: Mit `handoff_to_agent` antwortet der genannte Agent Ihnen im selben Turn direkt und behält danach das Gespräch. Ein Trenner im Transkript zeigt, wer übernommen hat und warum, und die obere Leiste wechselt zum neuen Agenten. Was der neue Agent vom Gespräch sieht, wird pro Agent festgelegt (das ganze Gespräch, eine Zusammenfassung, die letzten Nachrichten oder nur Ihre letzte Nachricht). Siehe [handoffs](handoffs.md).

## Kosten und Genehmigungen

Alles, was echtes Geld kostet, hält an und fragt nach. Bietet ein Agent an, einen Flow, einen Loop, ein Szenario oder ein Team auszuführen, zeigt er die Schätzung und wartet auf ein klares Ja. "Klingt gut" ist keine Genehmigung, und die Agenten sind angewiesen, es als mehrdeutig zu behandeln.

## Gespräche mit dem Assistenten

Die Gespräche des [Assistenten](assistant.md), per Stimme oder Text, werden auch hier aufgelistet, als Text, mit dem Etikett **Assistent** und in jedem Workspace (sie gehören der Person, nicht einem Workspace). Sie sind nur lesbar: Fortgesetzt oder neu begonnen werden sie auf der Seite Assistent, und eine Zeile, die gesprochen wurde, ist als *gesprochen* markiert. Siehe [Assistent, auf der Chat-Seite](assistant.md#the-page).

## Telegram

Ein Chat lässt sich mit einem Telegram-Chat verbinden, sodass dasselbe Gespräch vom Telefon aus weitergeht. Siehe [telegram](telegram.md).

Verwandt: [agents](agents.md), [flows](flows.md), [teams](teams.md).
