from signals.core.check_data_freshness import *  # noqa: F401,F403

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except NameError:
        pass
