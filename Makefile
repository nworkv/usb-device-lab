CC ?= cc
CFLAGS ?= -O2 -Wall -Wextra -Werror

all: build/kcov-remote
build/kcov-remote: host/kcov_remote.c
	mkdir -p build
	$(CC) $(CFLAGS) $< -o $@
test:
	python3 -m unittest discover -s tests -v
clean:
	rm -rf build
.PHONY: all test clean
