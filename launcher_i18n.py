"""
English texts for the launcher window (the code writes German; tr() translates for display).

Language: settings "lang" ("de"/"en"), else the Windows UI language (German -> de, else en).
tr() replaces known German phrases (longest first, whole words for single words) and leaves
values such as ports, names and file names untouched. Lines starting with "[" (output of the
server/proxy child processes) and tracebacks are not translated.
"""
import re

LANG = "de"

# (German, English) - phrases; order does not matter, longer ones are applied first
PHRASES = [
    # buttons / labels
    ("Update verfügbar – hier laden", "Update available – get it here"),
    ("Spiel neu starten", "Restart game"),
    ("Garena (offiziell)", "Garena (official)"),
    ("Wiederherstellen", "Restore"),
    ("LDPlayer laden", "Get LDPlayer"),
    ("Verbinden", "Connect"),
    ("Übernehmen", "Apply"),
    ("leer = lokal, sonst host[:port]", "empty = local, else host[:port]"),
    ("Aktiver Modus:", "Active mode:"),
    ("Emulator noch:", "emulator still:"),
    # status line
    ("läuft (extern)", "running (external)"),
    ("Relay-Fehler", "relay error"),
    ("nicht gefunden", "not found"),
    ("Neustart (Root) ...", "restarting (root) ..."),
    ("startet ...", "starting ..."),
    ("verbinde ...", "connecting ..."),
    ("kein Root", "no root"),
    ("nicht installiert", "not installed"),
    ("keine Verbindung", "no connection"),
    ("Zertifikat-Fehler", "certificate error"),
    ("verbunden", "connected"),
    ("läuft (Port", "running (port"),
    # log messages
    ("Bitte warten, es läuft noch ein Vorgang ...", "Please wait, something is still running ..."),
    ("FEHLER:", "ERROR:"),
    ("Admin Mode freigeschaltet.", "Admin Mode unlocked."),
    ("Server: lokal", "Server: local"),
    ("Modus gewechselt:", "Mode switched:"),
    ("ist schon belegt – nutze den laufenden Server.", "is already in use – using the running server."),
    ("ist schon belegt – nutze den laufenden Proxy.", "is already in use – using the running proxy."),
    ("ist nicht erreichbar (läuft er? Port offen?).", "cannot be reached (is it running? port open?)."),
    ("erreichbar.", "reachable."),
    ("Relay ist nicht gestartet (siehe Log).", "Relay did not start (see log)."),
    ("nicht gefunden in", "not found in"),
    ("Game-Server gestartet.", "Game server started."),
    ("Game-Server ist nicht gestartet (siehe Log).", "Game server did not start (see log)."),
    ("Proxy gestartet.", "Proxy started."),
    ("Proxy ist nicht gestartet (siehe Log).", "Proxy did not start (see log)."),
    ("Kein LDPlayer gefunden. Bitte LDPlayer 9 oder 14 installieren (Knopf „LDPlayer laden“), "
     "Haikyuu Fly High darin installieren, dann „Verbinden“.",
     "No LDPlayer found. Install LDPlayer 9 or 14 (button “Get LDPlayer”), install Haikyuu Fly High "
     "in it, then “Connect”."),
    ("Starte LDPlayer", "Starting LDPlayer"),
    ("Instanz", "instance"),
    ("Emulator verbunden", "Emulator connected"),
    ("Schalte Root ein und starte LDPlayer neu ...", "Switching on root and restarting LDPlayer ..."),
    ("Kein Root im Emulator. In LDPlayer: Einstellungen → Sonstiges → Root-Berechtigung: An, "
     "dann LDPlayer neu starten.",
     "No root in the emulator. In LDPlayer: Settings → Other → Root permission: On, then restart "
     "LDPlayer."),
    ("Root ist jetzt an.", "Root is on now."),
    ("Haikyuu Fly High ist in diesem Emulator nicht installiert.",
     "Haikyuu Fly High is not installed in this emulator."),
    ("Emulator antwortet nicht über adb.", "The emulator does not answer via adb."),
    ("Proxy im Emulator setzen fehlgeschlagen:", "Setting the proxy in the emulator failed:"),
    ("mitmproxy-Zertifikat fehlt (Proxy läuft nicht?).", "mitmproxy certificate missing (proxy not running?)."),
    ("Zertifikat-Upload fehlgeschlagen:", "Certificate upload failed:"),
    ("Proxy-Zertifikat im Emulator aktiv", "Proxy certificate active in the emulator"),
    ("Zertifikat-Mount fehlgeschlagen:", "Certificate mount failed:"),
    ("iptables fehlgeschlagen:", "iptables failed:"),
    ("Umleitung auf den Private Server aktiv.", "Redirect to the private server active."),
    ("Umleitung entfernt – das Spiel verbindet sich mit Garena.",
     "Redirect removed – the game connects to Garena."),
    ("Spiel neu gestartet.", "Game restarted."),
    ("Spiel gestartet.", "Game started."),
    ("Spiel starten fehlgeschlagen:", "Starting the game failed:"),
    ("Spiel lief schon im richtigen Modus.", "The game was already running in the right mode."),
    ("Fertig – viel Spaß!", "Done – have fun!"),
    ("Backup der Spielstände", "Backup of the save games"),
    ("beim Start", "at start"),
    ("von Hand", "by hand"),
    ("vor dem Wiederherstellen", "before restoring"),
    ("vor dem Update", "before the update"),
    ("Backup fehlgeschlagen:", "Backup failed:"),
    ("Keine Spielstände zum Sichern gefunden.", "No save games to back up."),
    ("Neue Version verfügbar", "New version available"),
    ("Knopf „Update verfügbar“.", "button “Update available”."),
    ("Desktop-Verknüpfung angelegt.", "Desktop shortcut created."),
    ("Verknüpfung fehlgeschlagen:", "Shortcut failed:"),
    ("Lade", "Downloading"),
    ("Update fehlgeschlagen: die Zip enthält keinen Launcher.", "Update failed: the zip contains no launcher."),
    ("Update fehlgeschlagen:", "Update failed:"),
    ("Update geladen – der Launcher startet gleich neu.", "Update downloaded – the launcher restarts now."),
    ("enthält keine Spielstände.", "contains no save games."),
    ("Spielstände aus", "Save games from"),
    ("wiederhergestellt.", "restored."),
    ("Admin-Rechte gespeichert", "Admin rights saved"),
    ("Admin Mode gesperrt (neu freischalten: Passwort im Spiel als Geschenkcode).",
     "Admin Mode locked (unlock again: password in game as gift code)."),
    ("Änderung(en) für Konto", "change(s) for account"),
    ("vorgemerkt.", "queued."),
    # dialogs
    ("Verknüpfung „Haikyuu Fly High“ auf dem Desktop anlegen?",
     "Create the desktop shortcut “Haikyuu Fly High”?"),
    ("Neue Version jetzt automatisch installieren?", "Install the new version automatically now?"),
    ("Der Launcher lädt sie herunter (ca. 50 MB), beendet sich, ersetzt die Programmdateien und startet "
     "neu. Deine Spielstände und Backups bleiben.",
     "The launcher downloads it (about 50 MB), closes, replaces the program files and starts again. "
     "Your save games and backups stay."),
    ("LDPlayer hat keine Root-Berechtigung, die der Launcher für die Umleitung braucht.",
     "LDPlayer has no root permission, which the launcher needs for the redirect."),
    ("Soll der Launcher LDPlayer kurz beenden, Root einschalten und neu starten?",
     "Shall the launcher close LDPlayer, switch on root and start it again?"),
    ("Der Server läuft gerade außerhalb des Launchers (Port 10104). Bitte diesen Server zuerst beenden, "
     "sonst überschreibt er die wiederhergestellten Spielstände.",
     "A server outside the launcher is running (port 10104). Stop it first, otherwise it overwrites the "
     "restored save games."),
    ("wiederherstellen?", "restore?"),
    ("Der aktuelle Stand wird vorher als Backup gesichert. Der Server wird dafür kurz neu gestartet.",
     "The current state is backed up first. The server restarts briefly."),
    ("Backup wiederherstellen", "Restore backup"),
    ("Sprache: Deutsch – wird beim nächsten Start übernommen.", "Language: German – applied at the next start."),
    ("Sprache: Englisch – wird beim nächsten Start übernommen.", "Language: English – applied at the next start."),
    # Admin Mode window
    ("Charaktere & Gegenstände", "Characters & items"),
    ("Übernehmen & Spiel neu starten", "Apply & restart game"),
    ("Admin Mode sperren", "Lock Admin Mode"),
    ("Manager-Level setzen:", "Set manager level:"),
    ("Währungen (Haken = auf diesen Wert setzen):", "Currencies (tick = set to this value):"),
    ("Unendlich Ausdauer:", "Infinite stamina:"),
    ("unverändert", "unchanged"),
    ("Alle Charaktere freischalten (je eine Karte von jedem, den du noch nicht hast)",
     "Unlock all characters (one card of each you don't have yet)"),
    ("Alle Karten auf 6 Sterne und Level 80", "All cards to 6 stars and level 80"),
    ("Alle Gegenstände auf Anzahl:", "All items to amount:"),
    ("Alle Sorten aus dem normalen Inventar (Tickets, Material, Geschenke …).",
     "Every kind from the normal inventory (tickets, materials, gifts …)."),
    ("Seltenheit:", "Rarity:"),
    ("Hauptwert:", "Main stat:"),
    ("Nebenwerte:", "Sub stats:"),
    ("Anzahl:", "Amount:"),
    ("Stufe +", "Level +"),
    ("Hinzufügen", "Add"),
    ("Ganzes Set (alle 6 Plätze, max)", "Whole set (all 6 slots, max)"),
    ("Liste leeren", "Clear list"),
    ("Diese Kombination gibt es im Spiel nicht.", "This combination does not exist in the game."),
    ("Stärke im Match: Prozent für alle Werte und/oder einzelne Werte direkt setzen (leer = unverändert). "
     "Gilt für Matches, nicht für die Anzeige im Kartenmenü.",
     "Match strength: percent for all stats and/or set single stats (empty = unchanged). "
     "Applies to matches, not to the card menu display."),
    ("Alle Werte in %:", "All stats in %:"),
    ("Karte zurücksetzen", "Reset card"),
    ("Alle Karten (*)", "All cards (*)"),
    ("Alle Karten", "All cards"),
    ("Karten konnten nicht geladen werden:", "Cards could not be loaded:"),
    ("Erst „Laden“ drücken und eine Karte wählen.", "Press “Load” first and choose a card."),
    ("Ungültiger Wert:", "Invalid value:"),
    ("Ungültiger Wert für Währung", "Invalid value for currency"),
    ("Kein freigeschaltetes Konto.", "No unlocked account."),
    ("Nichts ausgewählt.", "Nothing selected."),
    ("Gespeichert – Spiel startet neu, Änderungen kommen beim Login an. Im Spiel einmal auf START tippen.",
     "Saved – the game restarts, the changes arrive at login. Tap START in the game once."),
    ("Änderungen werden beim nächsten Login übernommen.", "Changes are applied at the next login."),
    ("Zuletzt übernommen", "Last applied"),
    ("Gemeinsamer Server: nur der Besitzer und die hier angehakten Spieler haben Admin-Rechte. Alle anderen "
     "spielen normal. Spieler mit Rechten können im Spiel unter Gift Code Befehle eingeben, z. B. /gold 100000, "
     "/allcards, /power 200, /gold 5000 @Name. Jedes Konto ist an den Garena-Login gebunden, der es zuerst "
     "benutzt hat; ein abgelehnter Login (z. B. neues Handy) lässt sich hier freigeben.",
     "Shared server: only the owner and the players ticked here have admin rights. Everybody else plays "
     "normally. Players with rights can enter commands under Gift Code in game, e.g. /gold 100000, /allcards, "
     "/power 200, /gold 5000 @Name. Every account is bound to the Garena login that used it first; a refused "
     "login (e.g. new phone) can be approved here."),
    ("Rechte speichern", "Save rights"),
    ("Neu laden", "Reload"),
    ("Noch kein Besitzer: im Spiel das Admin-Passwort als Gift Code eingeben.",
     "No owner yet: enter the admin password as gift code in game."),
    ("Besitzer:", "Owner:"),
    ("(Besitzer)", "(owner)"),
    ("(ohne Namen)", "(no name)"),
    ("Login abgelehnt", "Login refused"),
    ("Freigeben", "Approve"),
    ("gebunden", "bound"),
    ("Lösen", "Release"),
    ("freigegeben – der Spieler kann sich jetzt anmelden.", "approved – the player can log in now."),
    ("Login von", "Login of"),
    ("Bindung von", "Binding of"),
    ("lösen? Der nächste Login mit dieser ID wird dann als neuer Besitzer des Kontos gespeichert.",
     "release? The next login with this ID is then stored as the account's new owner."),
    ("gelöst.", "released."),
    ("Rechte gespeichert:", "Rights saved:"),
    ("Admin(s) neben dem Besitzer.", "admin(s) besides the owner."),
    ("Stufe 1 (max Lv. 6)", "Tier 1 (max Lv. 6)"),
    ("Stufe 2 (max Lv. 6)", "Tier 2 (max Lv. 6)"),
    ("Stufe 3 (max Lv. 9)", "Tier 3 (max Lv. 9)"),
    ("Stufe 4 (max Lv. 12)", "Tier 4 (max Lv. 12)"),
    ("Diamanten", "Diamonds"),
    ("Ausdauer (Onigiri)", "Stamina (onigiri)"),
    ("Schnellangriff", "Quick attack"),
    ("Kraftangriff", "Power attack"),
    ("Zuspiel", "Set"),
    ("Annahme", "Receive"),
    ("Aufschlag", "Serve"),
    ("Retten", "Save"),
    ("normal:", "normal:"),
    ("Platz", "Slot"),
    ("Stufe", "Tier"),
    ("Karte:", "Card:"),
    ("Konto:", "Account:"),
    ("Konto", "Account"),
    ("Stärke", "Strength"),
    ("Rechte", "Rights"),
    ("Laden", "Load"),
    ("an", "on"),
    ("aus", "off"),
    ("ERF", "EXP"),
    # game install
    ("Haikyuu Fly High ist in diesem LDPlayer nicht installiert.",
     "Haikyuu Fly High is not installed in this LDPlayer."),
    ("Hast du die Spieldatei (.apk, .xapk, .apks oder .apkm)? Dann wähle sie im nächsten Fenster aus, "
     "der Launcher installiert sie.",
     "Do you have the game file (.apk, .xapk, .apks or .apkm)? Choose it in the next window and the "
     "launcher installs it."),
    ("installiere ...", "installing ..."),
    ("Entpacke", "Unpacking"),
    ("enthält keine APK.", "contains no APK."),
    ("Installiere Haikyuu Fly High", "Installing Haikyuu Fly High"),
    ("Teil(e)) – das dauert ein paar Minuten ...", "part(s)) – this takes a few minutes ..."),
    ("Installation fehlgeschlagen:", "Installation failed:"),
    ("Haikyuu Fly High installiert.", "Haikyuu Fly High installed."),
    ("installiert", "installed"),
    # single words (whole words only)
    ("Spiel", "Game"),
    ("läuft", "running"),
    ("Fehler", "error"),
    ("fehlt", "missing"),
]
_TABLE = sorted(PHRASES, key=lambda p: -len(p[0]))


def detect(settings: dict) -> str:
    if settings.get("lang") in ("de", "en"):
        return settings["lang"]
    try:
        import ctypes
        lang = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF
        return "de" if lang == 0x07 else "en"
    except Exception:
        return "de"


def tr(text: str) -> str:
    if LANG == "de" or not text or text.startswith("[") or text.startswith("Traceback"):
        return text
    out = text
    for de, en in _TABLE:
        if " " in de or not de[0].isalpha():
            out = out.replace(de, en)
        else:
            out = re.sub(rf"(?<!\w){re.escape(de)}(?!\w)", en, out)
    return out


def patch_messagebox():
    """Translate title and text of the launcher's tkinter dialogs."""
    from tkinter import messagebox
    for name in ("askyesno", "showwarning", "showerror", "showinfo"):
        orig = getattr(messagebox, name)
        if getattr(orig, "_hfh_tr", False):
            continue

        def wrapped(title=None, message=None, _orig=orig, **kw):
            return _orig(tr(title or ""), tr(message or ""), **kw)
        wrapped._hfh_tr = True
        setattr(messagebox, name, wrapped)


def translate_tree(widget):
    """Translate the "text" of every widget below this one (no-op in German)."""
    if LANG == "de":
        return
    for child in widget.winfo_children():
        try:
            text = child.cget("text")
            if text:
                child.configure(text=tr(text))
        except Exception:
            pass
        translate_tree(child)
