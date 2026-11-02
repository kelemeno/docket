"""docket setup [--name NAME]   pick Claude's read-only tools (and how the prompts call you)
docket run                    the page at http://127.0.0.1:8766, and a refresh every hour 08:00-20:00
docket refresh                read what's new now
docket now                    the top of today's list
docket open                   open the page"""
import argparse
import sys
import webbrowser

from . import aag, app, store, tasks


def main(argv=None):
    p = argparse.ArgumentParser(prog="docket", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["setup", "run", "refresh", "now", "open"])
    p.add_argument("--name", help="setup: your first name, as the prompts call you")
    a = p.parse_args(argv)
    if a.command == "setup":
        allowed, refused = app.setup(a.name)
        print(f"Allowed (read-only): {len(allowed)}\n  " + "\n  ".join(allowed))
        print(f"Refused: {len(refused)}\n  " + "\n  ".join(refused))
        print(f"Kept in {store.SETTINGS}. Check the allowed list; edit the file to change it.")
    elif a.command == "run":
        try:
            aag.start()
            app.run()
        except KeyboardInterrupt:
            pass
    elif a.command == "refresh":
        reg = app.refresh()
        print(f"{sum(t['status'] in tasks.OPEN for t in reg['tasks'])} open tasks")
    elif a.command == "now":
        for t in tasks.order(tasks.load()["tasks"], store.today())[:8]:
            print(f"{t['priority']}  {t['title']}" + (f"\n    → {t['next']}" if t.get("next") else ""))
    elif a.command == "open":
        webbrowser.open(f"http://127.0.0.1:{app.PORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
