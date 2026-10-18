# Was dieser Dienst ist

Ein Agent Hub: ein Ort, an dem Sie KI-Agenten definieren, ihnen Werkzeuge und Speicher geben und sie ausführen, einzeln oder in Gruppen, an echter Arbeit in einem Workspace.

## Wie die Objekte ineinander liegen

Fast die gesamte Lernkurve besteht darin, zu wissen, was was enthält.

```
Workspace                  ein isolierter Ordner mit eigenen Agenten, Einstellungen und eigenem Speicher
 ├── Projekt               eine Codebasis oder ein Vorhaben innerhalb des Workspace
 │    └── Aufgabe          eine Arbeitseinheit, mit Unteraufgaben und Abhängigkeiten
 ├── Agent                 eine Definition: Prompt + Werkzeuge + Modell + Speicher
 ├── Flow                  ein Graph aus Agenten, der als eine Pipeline läuft
 │    └── Loop             ein Flow, der wiederholt wird, bis ein Judge ihn für gut genug hält
 ├── Team                  eine Besetzung von Agenten, die über ein gemeinsames Board arbeiten
 └── Ansicht               ein Diagramm, ein Graph oder eine 3D-Szene, die ein Agent gebaut hat
```

Jede dieser Arbeiten erzeugt dieselben drei Datensätze. Nur so lässt sich das System überhaupt beobachten:

- **Run**: ein Aufruf eines Agenten. Das Atom. Alles besteht aus Runs.
- **Session**: ein Gespräch, also die Runs, die zusammengehören.
- **Instanz**: eine Live-Kopie eines Agenten, mit eigenem Zustand und eigenem Verlauf.

## Zwei Arten von Agenten

**System-Agenten** werden mit dem Produkt ausgeliefert. Sie sind in jedem Workspace vorhanden und lassen sich dort nicht entfernen. Ihre Werkzeuge und Beschreibungen bleiben mit dem abgeglichen, was das Produkt ausliefert. Sie sorgen dafür, dass der Dienst ohne weitere Einrichtung funktioniert. Siehe [system-agents](system-agents.md).

**Eigene Agenten** gehören Ihnen. Sie legen sie an und fügen sie den Workspaces hinzu, die Sie möchten. Nichts, was das Produkt ausliefert, verändert sie.

## Wo die Arbeit tatsächlich stattfindet

- **Chat** ist die Eingangstür: Sie sprechen direkt mit jedem Agenten, ohne Einrichtung.
- **Aufgaben** sind für nachverfolgte Arbeit: zugewiesen, in Stufen gegliedert, fortsetzbar.
- **Flows, Loops und Teams** sind für Arbeit, die mehr als einen Agenten oder mehr als einen Durchgang braucht.

## Das Menü

Die Seitenleiste folgt demselben Baum in fünf Gruppen: **Gespräch** (Chat, Dashboard), **Arbeit** (Projekte, Aufgaben, der Plan, Artefakte, Flows, Loops, Teams, Workspaces), **Agenten und Bibliothek** (Agenten, Modelle, Skills, Speicher, der Marktplatz), **Integrationen** (Konnektoren) und **Protokolle** (Sitzungen, Läufe, Kosten, Evaluierungen, Einstellungen).

Es gibt zwei Modi. Das **einfache Menü** behält fünfzehn Seiten, die ein Einsteiger braucht, darunter die Workspaces, dazu Einstellungen und Dokumentation. Das Dashboard fehlt darin: Wer den Hub für sich selbst nutzt, hat dort wenig zu beobachten. Es ist die Voreinstellung. Das **vollständige Menü** zeigt jede Seite. Der Schalter am Fuß des Menüs wechselt zwischen beiden, und der Browser merkt sich die Wahl. In keinem Modus wird eine Seite entfernt, und das einfache Menü ändert nie seine Form: Eine Seite außerhalb davon hebt die Zeile hervor, zu der sie gehört (Deployments, von der Plan-Seite geöffnet, heben Plan hervor, Loops und Teams heben Agenten-Flows hervor, Konnektoren heben Einstellungen hervor, ebenso Dashboard, Sitzungen und Kosten).

## Zwei Dinge, die Sie früh wissen sollten

**Geld ist real.** Jeder Run kostet Tokens. Ein Team bedeutet: Mitglieder mal Runden. Ein Loop bedeutet: ein ganzer Flow, wiederholt. Die Werkzeuge, die diese Arbeiten starten, lehnen ab, bis Sie sie genehmigt haben, und die Ablehnung zeigt die Schätzung.

**Fähigkeiten werden durchgesetzt.** Ein Agent, der Ihre privaten Daten lesen kann, Text von außen aufnimmt *und* Daten nach außen sendet, ist ein Werkzeug zum Datenabfluss. Das Produkt lehnt diese Kombination ab, statt nur davor zu warnen. Siehe [tools-and-capabilities](tools-and-capabilities.md).
