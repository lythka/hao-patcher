# ripatch: RIDIFF10 image patcher (host libParallelCompression via dlopen). Build: make  (or the cc line below)
#   cc -O2 -Wall -o build/ripatch ripatch.c
# Output goes to build/ (not versioned). No library is linked: otool -L build/ripatch shows libSystem only.
CC ?= cc
CFLAGS ?= -O2 -Wall
build/ripatch: ripatch.c
	mkdir -p build
	$(CC) $(CFLAGS) -o $@ ripatch.c
clean:
	rm -rf build
.PHONY: clean
