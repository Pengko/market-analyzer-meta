from signals.research.run_next_day_bias_suite import *  # noqa: F401,F403

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except NameError:
        pass
