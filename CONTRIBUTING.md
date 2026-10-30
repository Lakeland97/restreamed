# Contributing

## Ground rules

- Do not commit or attach game files, extracted assets, decompiled dumps, or binaries.
- Keep changes focused. One concern per pull request.
- State what you ran when you report a result.

## Running things

Plain Python 3.9+, no third-party dependencies.

  python3 mock_appserver.py 8096
  python3 tests/run_all.py

The tests are offline and must pass before a pull request is merged. CI runs the same
command on Linux.

## Protocol work

The wire format is documented by `tools/haxe_ser.py` (Haxe remoting) and
`tools/hxinflate.py` (entity type-info streams). Captures of real requests and replies are
useful; remove personal data before posting. Design notes are in the wiki.
