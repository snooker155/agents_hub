# Modelle

Die Seite Modelle ist die maßgebliche Quelle dafür, welche Modelle es gibt, welche angeboten werden, was sie kosten und welches pro Anbieter das Standardmodell ist.

## Katalog und aktivierte Modelle

Ein Anbieter stellt viele Modelle bereit. Der Katalog kürzt diese Rohliste auf die Modelle, die Sie tatsächlich anbieten möchten: **Nur aktivierte Modelle erscheinen in der Auswahl.**

## Standards und Überschreibungen

Reihenfolge der Auflösung, das Spezifischste zuerst:

1. das eigene, festgelegte Modell des Agenten
2. die ausdrückliche Modellüberschreibung des Workspace
3. das Standardmodell des Workspace
4. der globale Standardanbieter und sein Modell

Ein Agent ohne festgelegtes Modell erbt es. Meistens ist das genau, was Sie wollen: Ändern Sie das Standardmodell des Workspace, und der ganze Workspace zieht mit.

Hat ein Workspace eine [Modellrichtlinie](#modellrichtlinie-pro-workspace), beschränkt sie all das auf die Modelle, die sie erlaubt.

## Modellrichtlinie pro Workspace

Ein Administrator kann festlegen, mit welchen Modellen und Einstellungen ein Workspace arbeitet, sodass seine Mitglieder sie nicht ändern können. Die Richtlinie wird auf der Seite des Workspace gesetzt (Reiter Einstellungen) oder mit `PUT /api/workspaces/{name}/model-policy`:

```json
{"models": ["openai/gpt-5", "anthropic/claude-sonnet-5-5"],
 "default": "openai/gpt-5",
 "temperature": 0.2, "effort": "medium", "max_tokens": 8000}
```

`models` sind aktivierte Katalogmodelle; `default` muss eines davon sein (ohne Angabe das erste). `temperature`, `effort` und `max_tokens` sind optional. Eine leere Liste `models` entfernt die Richtlinie. Im Modus `multi` schreibt sie nur ein Administrator, auch der Eigentümer des Workspace nicht; lesen dürfen alle Mitglieder (`GET` auf demselben Pfad und `policy` in `GET /api/workspaces/{name}/model`).

Solange eine Richtlinie gilt:

- ist das Standardmodell des Workspace das der Richtlinie, und die Route für das Standardmodell lehnt Änderungen ab;
- bietet die Modellauswahl in der Kopfzeile nur die Modelle der Richtlinie an, das globale Standardmodell gehört nicht dazu;
- lässt sich das eigene Modell eines Agenten nur auf ein erlaubtes Modell seines Workspace setzen (und des Workspace, in dem die Seite offen ist);
- wählen ein Modellwechsel in einem laufenden Lauf, die Ersatzmodelle eines Agenten, der Berater und eine Delegation mit benanntem Modell nur unter den erlaubten Modellen (`tools/delegation.py`, `enabled_models`, `resolve_model`);
- läuft ein nicht erlaubtes Modell (ein Agent, der vor der Richtlinie festgelegt wurde, ein geteilter oder System-Agent mit einem Modell für einen anderen Workspace) stattdessen auf dem Standardmodell der Richtlinie, mit Endpunkt und Schlüssel von dessen Anbieter (`agents/agent_factory.py`);
- schlagen `temperature`, `effort` und `max_tokens`, wenn gesetzt, die Werte des Agenten und den Aufwand aus der Kopfzeile.

Eine Richtlinie mit einem einzigen Modell macht dieses Modell zum globalen Modell des Workspace: Standardmodell, einzige Wahl in der Kopfzeile und einziges Modell, auf dem dort jeder Agent läuft.

Die Richtlinie legt auch die [Spezialmodelle](special-models.md) des Workspace (Bilder, Video, Sprache, Transkription und eigene Modelle) in die Hand des Administrators: Mitglieder sehen sie nur lesend. Mit `"lock_special_models": false` bleiben sie den Mitgliedern überlassen.

Ein persönlicher Workspace eines Mitglieds ohne eigene Richtlinie folgt der des Workspace `default` (`inherited_from: "default"` in der Antwort), sodass die Arbeit im eigenen Workspace die Richtlinie des Hubs nicht umgeht. Ein Administrator kann einem persönlichen Workspace eine eigene Richtlinie geben, die dann stattdessen gilt.

Beim Speichern einer Richtlinie wird die Wahl in der Kopfzeile verworfen, wenn die Richtlinie sie nicht erlaubt. Den Modellkatalog selbst (welche Modelle aktiviert sind, ihre Preise, Temperaturen und Aufwände) ändert im Modus `multi` nur ein Administrator.

## Ein erster Schlüssel schaltet ein Modell frei

Früher blieb nach dem Speichern eines Anbieterschlüssels (in den Einstellungen, im Willkommensfenster oder auf der Verbindungskarte des Assistenten) vor dem ersten Run noch ein Gang zu dieser Seite übrig: ein Modell aktivieren und seinen Preis eintragen. Jetzt aktiviert der Hub ein Standardmodell für den Anbieter, wenn dieser noch kein aktiviertes Modell hat (`common/default_model.py`, eine Tabelle: `gpt-5.4-mini` für OpenAI, `claude-sonnet-5-5` für Anthropic, `gemini-2.5-flash` für Google). Er trägt dessen Katalogpreis ein und markiert es mit einem Stern als Standardmodell des Anbieters. Hat der Hub noch keinen globalen Standardanbieter oder hat der aktuelle keinen Schlüssel, macht er diesen Anbieter zum globalen Standard und schreibt `DEFAULT_PROVIDER` sowie das `*_MODEL` des Anbieters in die `.env`. Eine bestehende Wahl überschreibt er nie: Ein Anbieter mit einem aktivierten Modell bleibt unangetastet, und ein globaler Standard mit Schlüssel bleibt bestehen. Ein Schlüssel, der in einem anderen Workspace gespeichert wird, aktiviert das Modell, rührt aber den globalen Standard nicht an. Die Einstellungen zeigen, was freigeschaltet wurde, mit dem Preis und einem Link hierher. Ändern können Sie es wie gewohnt auf dieser Seite.

## Preise

Jedes Modell hat drei Preise in USD pro Million Tokens: **Eingabe**, **zwischengespeicherte Eingabe** und **Ausgabe**. Damit werden die aufgezeichneten Token-Mengen auf der Seite [costs](costs.md) in geschätzte Ausgaben umgerechnet. Ein Modell ohne Preis liefert Tokens, aber keine Kosten. Eine Summe von null bedeutet also "ohne Preis", nicht "kostenlos".

Die zwischengespeicherte Eingabe hat einen eigenen Preis, weil der Anbieter sie in einer eigenen Zeile abrechnet. Eine Agentenschleife sendet bei jedem Schritt das ganze Gespräch erneut. Der Anbieter liefert alles außer der ersten Kopie dieses Präfixes aus seinem Prompt-Cache, zu einem Bruchteil des Eingabepreises. Ein Modell ohne eigene Angabe erhält ein Zehntel seines Eingabepreises, so viel berechnen die großen Anbieter. Setzen Sie das Feld, wo Ihr Anbieter davon abweicht.

Jede Zeile hält außerdem fest, **woher ihr Preis stammt**: `auto` für einen Wert, der bei der Erkennung aus der mitgelieferten Tabelle eingetragen wurde, `manual`, sobald Sie ihn bearbeitet haben. Ein bearbeiteter Preis wird von einer späteren Erkennung nie überschrieben, und die Seite markiert ihn.

## Kontextfenster

Ein Modell kann sein Kontextfenster mitführen. An dieser Zahl misst sich die Kontextanzeige im Chat. Ein Fenster von `0` bedeutet "unbekannt"; die Anzeige zeichnet dann nichts, statt eine Obergrenze zu erfinden.

## Temperatur

Jedes Modell kann in seiner Zeile eine eigene Temperatur tragen. Ein leeres Feld bedeutet die globale Temperatur, die als Platzhalter angezeigt wird. Reihenfolge der Auflösung, das Spezifischste zuerst:

1. die eigene Temperatur des Agenten (seine Modellüberschreibung)
2. die Temperatur des Modells auf dieser Seite
3. die globale Temperatur: Einstellungen, Modelle, Temperatur (`LLM_TEMPERATURE` in der `.env`,
   0.0, wenn nichts gesetzt ist), vom laufenden Backend sofort angewendet

Eine Ebene für den Workspace gibt es nicht.

Manche Modelle lehnen eine Temperatur ab. Die Reasoning-Modelle von OpenAI nehmen beim Nachdenken gar keine an, deshalb ist das Feld für gpt-5, gpt-5-mini, gpt-5-nano und die o-Serie deaktiviert. gpt-5.1 und neuer nehmen eine nur bei Reasoning-Aufwand `none` an. Diesen sendet ein Agent mit ausgeschaltetem Nachdenken (siehe unten), die Temperatur gilt für diese Modelle also nur, solange das Nachdenken aus ist. Auch Claude nimmt beim Nachdenken keine an.

## Nachdenken

Die Spalte Nachdenken zeigt pro Modell zwei Dinge: den Aufwand, den der Anbieter anwendet, wenn eine Anfrage keinen nennt, und was der Hub sendet, wenn die Denkstufe eines Agenten aus ist. Beides unterscheidet sich, weil die Reasoning-Modelle von OpenAI weiterdenken, wenn nichts angefordert wird, berechnet und unsichtbar:

| Familie | Standard des Anbieters | Gesendet bei aus |
|---|---|---|
| gpt-5, gpt-5-mini, gpt-5-nano | mittel | minimal |
| gpt-5.1 bis gpt-5.4 | keiner | keiner |
| gpt-5.5, gpt-5.6 | mittel | keiner |
| o1, o3, o4 | mittel | niedrig (tiefer geht es nicht) |
| gpt-5-pro | hoch | nichts (tiefer geht es nicht) |
| Claude | aus | nichts |

Die Tabelle wurde gegen die API gemessen (`providers/reasoning_profile.py`). Anbieter ohne Eintrag zeigen einen Strich. Ein Aufruf, der nie eine Stufe gewählt hat, etwa ein Chat-Titel oder ein Eval-Judge, sendet nichts und behält den Standard des Anbieters. Was eine positive Stufe im Chat zeigt, steht in [agents](agents.md).

## Anbieter

API-Schlüssel und Basis-URLs liegen in den [Einstellungen](settings.md), nicht hier. Diese Seite behandelt, welche Modelle es gibt und was sie kosten. Lokale Anbieter (Ollama, LM Studio) und eigene OpenAI-kompatible Backends werden auf dieselbe Weise konfiguriert.

## Lokale Modelle

Ein Ollama, das Sie betreiben, und die eigene llama.cpp-Runtime des Hubs werden ebenfalls von dieser Seite aus verwaltet: Ollama-Modelle laden und löschen, GGUF-Dateien von Hugging Face herunterladen, sie laden und entladen. Ein Modell, das die Runtime lädt, wird hier unter dem Anbieter `hub-local` hinzugefügt und aktiviert, und beim Entladen wieder deaktiviert. Siehe [local models](local-models.md).

## Spezialmodelle

Bild-, Video-, Sprach- und Transkriptionsmodelle sowie die eigenen Modelle eines Workspace sind keine Chat-Modelle und stehen nicht im Katalog. Der Reiter **Spezialmodelle** wählt sie für den Workspace, der in der Kopfzeile gewählt ist, im selben Formular wie in den Einstellungen dieses Workspace. Siehe [special models](special-models.md).

Verwandt: [settings](settings.md), [costs](costs.md), [local models](local-models.md), [special models](special-models.md).
