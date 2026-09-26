#define _GNU_SOURCE
#include <ctype.h>
#include <errno.h>
#include <locale.h>
#include <math.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* A bounded expression parser. No interpreter, subprocess or global state. */
typedef struct {
    const char *p;
    const char *error;
    unsigned depth;
    locale_t locale;
} Parser;

static double expression(Parser *p);
static double unary(Parser *p);
static void spaces(Parser *p) { while (isspace((unsigned char)*p->p)) p->p++; }
static int take(Parser *p, char c) {
    spaces(p);
    if (*p->p != c) return 0;
    p->p++;
    return 1;
}
static double fail(Parser *p, const char *message) {
    if (!p->error) p->error = message;
    return 0.0;
}
static double atom(Parser *p) {
    if (p->error) return 0.0;
    if (++p->depth > 128) { p->depth--; return fail(p, "Expressao muito complexa"); }
    double result = 0;
    spaces(p);
    if (take(p, '(')) {
        result = expression(p);
        if (!take(p, ')')) fail(p, "Falta fechar parenteses");
    } else if (isdigit((unsigned char)*p->p) || *p->p == '.') {
        char *end;
        errno = 0;
        result = strtod_l(p->p, &end, p->locale);
        if (end == p->p || errno == ERANGE) fail(p, "Numero invalido ou fora do limite");
        p->p = end;
    } else if (isalpha((unsigned char)*p->p)) {
        char name[24];
        size_t n = 0;
        while (isalpha((unsigned char)*p->p)) {
            if (n < sizeof(name) - 1) name[n++] = *p->p;
            p->p++;
        }
        name[n] = '\0';
        if (!strcmp(name, "pi")) result = acos(-1);
        else if (!strcmp(name, "e")) result = exp(1);
        else if (take(p, '(')) {
            double a = expression(p);
            if (!take(p, ')')) fail(p, "Falta fechar parenteses");
            if (!strcmp(name, "sqrt")) result = sqrt(a);
            else if (!strcmp(name, "sin")) result = sin(a);
            else if (!strcmp(name, "cos")) result = cos(a);
            else if (!strcmp(name, "tan")) result = tan(a);
            else if (!strcmp(name, "asin")) result = asin(a);
            else if (!strcmp(name, "acos")) result = acos(a);
            else if (!strcmp(name, "atan")) result = atan(a);
            else if (!strcmp(name, "ln")) result = log(a);
            else if (!strcmp(name, "log")) result = log10(a);
            else if (!strcmp(name, "abs")) result = fabs(a);
            else if (!strcmp(name, "exp")) result = exp(a);
            else if (!strcmp(name, "floor")) result = floor(a);
            else if (!strcmp(name, "ceil")) result = ceil(a);
            else result = fail(p, "Funcao desconhecida");
        } else result = fail(p, "Nome desconhecido");
    } else result = fail(p, "Esperado um numero ou parenteses");
    p->depth--;
    return result;
}
static double power(Parser *p) {
    double a = atom(p);
    while (!p->error && take(p, '%')) a /= 100.0;
    if (!p->error && take(p, '^')) a = pow(a, unary(p));
    return a;
}
static double unary(Parser *p) {
    if (p->error) return 0;
    if (++p->depth > 128) { p->depth--; return fail(p, "Expressao muito complexa"); }
    double value;
    if (take(p, '+')) value = unary(p);
    else if (take(p, '-')) value = -unary(p);
    else value = power(p);
    p->depth--;
    return value;
}
static double product(Parser *p) {
    double a = unary(p);
    while (!p->error) {
        if (take(p, '*')) a *= unary(p);
        else if (take(p, '/')) {
            double b = unary(p);
            if (b == 0) return fail(p, "Divisao por zero");
            a /= b;
        } else break;
    }
    return a;
}
static double expression(Parser *p) {
    double a = product(p);
    while (!p->error) {
        if (take(p, '+')) a += product(p);
        else if (take(p, '-')) a -= product(p);
        else break;
    }
    return a;
}

int ayo_calculate(const char *input, double *out, char *error, size_t capacity) {
    if (!input || !out || !error || !capacity) return 1;
    error[0] = '\0';
    if (strnlen(input, 4097) > 4096) {
        snprintf(error, capacity, "Expressao muito longa");
        return 1;
    }
    locale_t locale = newlocale(LC_NUMERIC_MASK, "C", (locale_t)0);
    if (!locale) { snprintf(error, capacity, "Falha ao iniciar calculadora"); return 1; }
    Parser p = {.p = input, .locale = locale};
    double value = expression(&p);
    spaces(&p);
    if (!p.error && *p.p) p.error = "Caractere inesperado";
    if (!p.error && !isfinite(value)) p.error = "Resultado fora do dominio ou limite";
    freelocale(locale);
    if (p.error) { snprintf(error, capacity, "%s", p.error); return 1; }
    *out = value;
    return 0;
}
