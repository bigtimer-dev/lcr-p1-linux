import sys

if len(sys.argv) > 1 and sys.argv[1] == 'log':
    from .log import main
    sys.exit(main(sys.argv[2:]))
if len(sys.argv) > 1 and sys.argv[1] == 'cmd':
    from .log import cmd_main
    sys.exit(cmd_main(sys.argv[2:]))
if len(sys.argv) > 1 and sys.argv[1] == 'flash':
    from .flash import main
    sys.exit(main(sys.argv[2:]))
from .app import main
sys.exit(main())
