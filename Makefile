CC ?= cc
CFLAGS ?= -O2 -Wall -Wextra -Werror -std=c11
# The calculator's C library: libayo.so on Linux, ayo.dll on Windows (MSYS2 sets OS=Windows_NT).
ifeq ($(OS),Windows_NT)
NATIVE := build/ayo.dll
PIC :=
else
NATIVE := build/libayo.so
PIC := -fPIC
endif

.PHONY: all run test check native-check doctor install uninstall
all: $(NATIVE)

$(NATIVE): native/calc.c
	mkdir -p build
	$(CC) $(CFLAGS) $(PIC) -shared $< -o $@ $(LDFLAGS) -lm

run: all
	python3 -m ayo_desk

test: all
	python3 -m unittest discover -s tests -v

check: test
	python3 -m compileall -q ayo_desk

native-check:
	mkdir -p build
	$(CC) $(CFLAGS) -g -fsanitize=address,undefined native/calc.c native/test_calc.c -o build/calc-check -lm
	./build/calc-check

doctor:
	python3 scripts/doctor.py

install: all
	python3 scripts/install.py

uninstall:
	python3 scripts/install.py --uninstall
