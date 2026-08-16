import json


def main() -> int:
    payload = {
        "z": 1,
        "a": ["é", True],
    }
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    print(json.dumps("é", ensure_ascii=True))
    print(json.dumps("é", ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
