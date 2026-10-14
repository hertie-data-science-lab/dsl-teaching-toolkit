import re
import sys
from pathlib import Path

for f in sys.argv[1:]:
    p = Path(f)
    out = []
    for line in p.read_text().splitlines(keepends=True):
        if "contents/dsl-course.yml" in line or ".system/dsl-course.yml" in line:
            line = line.replace(
                "contents/dsl-course.yml", "contents/course.yml"
            ).replace(".system/dsl-course.yml", ".system/course.yml")
            line = re.sub(
                r"course_name: ([^\\`']+)\\ncourse_code: ([^\\`']+)\\n",
                r"course:\\n  name: \1\\n  code: \2\\n",
                line,
            )
            line = re.sub(
                r"course_name: ([^\\`']+)\\n", r"course:\\n  name: \1\\n", line
            )
        out.append(line)
    p.write_text("".join(out))
