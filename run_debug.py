import traceback, sys

class Tee:
    def __init__(self, *files):
        self.files = files
    def write(self, data):
        for f in self.files:
            f.write(data)
            f.flush()
    def flush(self):
        for f in self.files:
            f.flush()

log = open("game_log.txt", "w", encoding="utf-8")
sys.stdout = Tee(sys.__stdout__, log)
sys.stderr = Tee(sys.__stderr__, log)

try:
    import play_2048
    play_2048.main()
except Exception as e:
    traceback.print_exc()
    input("Press Enter to close...")
finally:
    log.close()
