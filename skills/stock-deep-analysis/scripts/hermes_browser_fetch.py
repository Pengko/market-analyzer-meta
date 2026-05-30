from fetchers.hermes_browser_fetch import *  # noqa: F401,F403

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except NameError:
        pass
