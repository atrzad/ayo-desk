#include <assert.h>
#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

int ayo_calculate(const char *, double *, char *, size_t);

int main(void) {
    char error[128];
    double result = 0;
    assert(ayo_calculate(NULL, &result, error, sizeof(error)) == 1);
    assert(ayo_calculate("1", NULL, error, sizeof(error)) == 1);
    assert(ayo_calculate("1", &result, NULL, sizeof(error)) == 1);
    assert(ayo_calculate("1", &result, error, 0) == 1);
    uint32_t seed = 81234;
    const char alphabet[] = "0123456789+-*/^%().esincotqrablfgpx ";
    for (unsigned sample = 0; sample < 25000; sample++) {
        char input[129];
        seed = seed * 1664525u + 1013904223u;
        unsigned length = seed % 128;
        for (unsigned i = 0; i < length; i++) {
            seed = seed * 1664525u + 1013904223u;
            input[i] = alphabet[seed % (sizeof(alphabet) - 1)];
        }
        input[length] = '\0';
        int code = ayo_calculate(input, &result, error, sizeof(error));
        assert(code == 0 || code == 1);
        if (!code) assert(isfinite(result));
    }
    puts("C parser: 25000 bounded fuzz cases passed");
    return 0;
}
