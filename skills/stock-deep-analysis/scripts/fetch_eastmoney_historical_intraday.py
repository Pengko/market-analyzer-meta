from fetchers.fetch_eastmoney_historical_intraday import *  # noqa: F401,F403

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except NameError:
        pass
