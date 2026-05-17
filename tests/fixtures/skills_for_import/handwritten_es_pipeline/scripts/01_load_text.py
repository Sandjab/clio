"""Read a text file and emit its content as JSON on stdout."""
import json
import sys

path = sys.argv[1]
print(json.dumps({"text": open(path).read()}))
