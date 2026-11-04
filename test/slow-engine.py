import sys
import time

# 假引擎：只为验证「同角色串行排队」，不读不写任何记忆。
# 参数形状与 role_core.py 一致：--reply <角色名> <正文> --speaker <谁说的>
# 所以「正文」是位置参数，不是 --reply 的值。
argv = sys.argv[1:]
takes_value = {"--reply", "--speaker", "--say", "--turn", "--doctor"}
text = ""
skip_next = False
for arg in argv:
    if skip_next:
        skip_next = False
        continue
    if arg in takes_value:
        skip_next = True
        continue
    if arg.startswith("-"):
        continue
    text = arg
time.sleep(1.5)
print("假引擎回话：" + text)
