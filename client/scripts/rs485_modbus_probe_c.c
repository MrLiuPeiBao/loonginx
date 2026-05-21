#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>

#ifdef __linux__
#include <linux/serial.h>
#endif

typedef struct {
    const char *name;
    int address;
    int function;
    int reg;
    int count;
    int timeout_ms;
    int post_tx_delay_ms;
} target_t;

static const target_t TARGETS[] = {
    {"env-temp", 15, 3, 0x0001, 1, 1800, 80},
    {"env-humi", 15, 3, 0x0002, 1, 1800, 80},
    {"env-smoke", 15, 3, 0x000b, 1, 1800, 80},
    {"co", 1, 3, 0x0065, 2, 1200, 80},
    {"h2s", 2, 3, 0x0065, 2, 1200, 80},
    {"o2", 3, 3, 0x0065, 2, 1200, 80},
    {"ch4", 4, 3, 0x0065, 2, 1200, 80},
    {"photo", 25, 2, 0x0000, 2, 250, 0},
    {"bms-voltage", 210, 3, 0x0028, 1, 1000, 50},
    {"bms-current", 210, 3, 0x0029, 1, 1000, 50},
    {"bms-soc", 210, 3, 0x002a, 1, 1000, 50},
    {"bms-status", 210, 3, 0x002f, 1, 1000, 50},
    {"bms-capacity", 210, 3, 0x0030, 1, 1000, 50},
    {"bms-power", 210, 3, 0x0039, 1, 1000, 50},
    {"bms-cells", 210, 3, 0x0000, 8, 1000, 50},
};

typedef struct {
    const char *port;
    int baud;
    int samples;
    int cycles;
    int gap_ms;
    int target_timeout_ms;
    int post_tx_delay_ms;
    int extra_retry;
    int retry_gap_ms;
    bool batch;
    bool rs485;
    bool keep_open;
    const char *targets_arg;
    const char *scenario;
} options_t;

typedef struct {
    char status[32];
    char first_status[32];
    int attempts;
    int ok_attempt;
    int rx_len;
    int expected_len;
    double elapsed_ms;
    uint8_t request[8];
    uint8_t response[512];
} txn_result_t;

static long long monotonic_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long long)ts.tv_sec * 1000000000LL + ts.tv_nsec;
}

static void sleep_ms(int ms) {
    if (ms <= 0) return;
    struct timespec ts;
    ts.tv_sec = ms / 1000;
    ts.tv_nsec = (long)(ms % 1000) * 1000000L;
    nanosleep(&ts, NULL);
}

static uint16_t crc16_modbus(const uint8_t *data, int len) {
    uint16_t crc = 0xffff;
    for (int i = 0; i < len; i++) {
        crc ^= data[i];
        for (int bit = 0; bit < 8; bit++) {
            if (crc & 1) {
                crc = (crc >> 1) ^ 0xa001;
            } else {
                crc >>= 1;
            }
        }
    }
    return crc;
}

static void print_hex_json(const uint8_t *data, int len) {
    for (int i = 0; i < len; i++) {
        if (i) printf(" ");
        printf("%02x", data[i]);
    }
}

static void json_escape(const char *value) {
    for (const char *p = value; *p; p++) {
        if (*p == '"' || *p == '\\') {
            putchar('\\');
            putchar(*p);
        } else if (*p == '\n') {
            printf("\\n");
        } else if (*p == '\r') {
            printf("\\r");
        } else if (*p == '\t') {
            printf("\\t");
        } else {
            putchar(*p);
        }
    }
}

static speed_t baud_to_constant(int baud) {
    switch (baud) {
        case 9600: return B9600;
        case 19200: return B19200;
        case 38400: return B38400;
        case 57600: return B57600;
        case 115200: return B115200;
        default: return B9600;
    }
}

static int open_serial(const options_t *opt) {
    int fd = open(opt->port, O_RDWR | O_NOCTTY | O_SYNC);
    if (fd < 0) return -1;

    struct termios tio;
    memset(&tio, 0, sizeof(tio));
    if (tcgetattr(fd, &tio) != 0) {
        close(fd);
        return -1;
    }
    cfmakeraw(&tio);
    cfsetispeed(&tio, baud_to_constant(opt->baud));
    cfsetospeed(&tio, baud_to_constant(opt->baud));
    tio.c_cflag |= (CLOCAL | CREAD);
    tio.c_cflag &= ~CSIZE;
    tio.c_cflag |= CS8;
    tio.c_cflag &= ~PARENB;
    tio.c_cflag &= ~CSTOPB;
    tio.c_cc[VMIN] = 0;
    tio.c_cc[VTIME] = 1;
    if (tcsetattr(fd, TCSANOW, &tio) != 0) {
        close(fd);
        return -1;
    }

#if defined(__linux__) && defined(TIOCSRS485)
    if (opt->rs485) {
        struct serial_rs485 rs485;
        memset(&rs485, 0, sizeof(rs485));
        rs485.flags |= SER_RS485_ENABLED;
        rs485.flags |= SER_RS485_RTS_ON_SEND;
        if (ioctl(fd, TIOCSRS485, &rs485) != 0) {
            fprintf(stderr, "warning: TIOCSRS485 failed: %s\n", strerror(errno));
        }
    }
#endif
    tcflush(fd, TCIOFLUSH);
    return fd;
}

static const target_t *find_target(const char *name) {
    size_t n = sizeof(TARGETS) / sizeof(TARGETS[0]);
    for (size_t i = 0; i < n; i++) {
        if (strcmp(TARGETS[i].name, name) == 0) return &TARGETS[i];
    }
    return NULL;
}

static int expected_len(const target_t *target) {
    int byte_count = 0;
    if (target->function == 1 || target->function == 2) {
        byte_count = (target->count + 7) / 8;
    } else {
        byte_count = target->count * 2;
    }
    return 3 + byte_count + 2;
}

static void build_request(const target_t *target, uint8_t request[8]) {
    request[0] = (uint8_t)target->address;
    request[1] = (uint8_t)target->function;
    request[2] = (uint8_t)((target->reg >> 8) & 0xff);
    request[3] = (uint8_t)(target->reg & 0xff);
    request[4] = (uint8_t)((target->count >> 8) & 0xff);
    request[5] = (uint8_t)(target->count & 0xff);
    uint16_t crc = crc16_modbus(request, 6);
    request[6] = (uint8_t)(crc & 0xff);
    request[7] = (uint8_t)((crc >> 8) & 0xff);
}

static const char *classify(const target_t *target, const uint8_t *rx, int rx_len, int exp_len) {
    if (rx_len <= 0) return "timeout";
    if (rx_len < 2) return "short_frame";
    if (rx[0] != (uint8_t)target->address) return "unexpected_address";
    if (rx[1] & 0x80) return "modbus_exception";
    if (rx[1] != (uint8_t)target->function) return "unexpected_function";
    if (rx_len < exp_len) return "short_frame";
    uint16_t got = (uint16_t)rx[rx_len - 2] | ((uint16_t)rx[rx_len - 1] << 8);
    if (crc16_modbus(rx, rx_len - 2) != got) return "crc_error";
    return "ok";
}

static int read_deadline(int fd, uint8_t *rx, int max_len, int exp_len, int timeout_ms) {
    int rx_len = 0;
    long long deadline = monotonic_ns() + (long long)timeout_ms * 1000000LL;
    while (rx_len < exp_len && rx_len < max_len) {
        long long now = monotonic_ns();
        int remaining_ms = (int)((deadline - now) / 1000000LL);
        if (remaining_ms <= 0) break;
        struct pollfd pfd;
        pfd.fd = fd;
        pfd.events = POLLIN;
        pfd.revents = 0;
        int poll_rc = poll(&pfd, 1, remaining_ms > 200 ? 200 : remaining_ms);
        if (poll_rc > 0 && (pfd.revents & POLLIN)) {
            ssize_t n = read(fd, rx + rx_len, max_len - rx_len);
            if (n > 0) rx_len += (int)n;
        } else if (poll_rc < 0 && errno != EINTR) {
            break;
        }
    }
    return rx_len;
}

static txn_result_t do_attempt(const options_t *opt, const target_t *target, int reuse_fd) {
    txn_result_t result;
    memset(&result, 0, sizeof(result));
    strcpy(result.status, "exception");
    build_request(target, result.request);
    result.expected_len = expected_len(target);
    long long start = monotonic_ns();
    int fd = reuse_fd >= 0 ? reuse_fd : open_serial(opt);
    if (fd < 0) {
        snprintf(result.status, sizeof(result.status), "open_error");
        result.elapsed_ms = (monotonic_ns() - start) / 1000000.0;
        return result;
    }
    tcflush(fd, TCIOFLUSH);
    ssize_t wrote = write(fd, result.request, 8);
    (void)wrote;
    tcdrain(fd);
    int post_tx_delay = opt->post_tx_delay_ms >= 0 ? opt->post_tx_delay_ms : target->post_tx_delay_ms;
    sleep_ms(post_tx_delay);
    int timeout_ms = opt->target_timeout_ms > 0 ? opt->target_timeout_ms : target->timeout_ms;
    result.rx_len = read_deadline(fd, result.response, (int)sizeof(result.response), result.expected_len, timeout_ms);
    snprintf(result.status, sizeof(result.status), "%s", classify(target, result.response, result.rx_len, result.expected_len));
    result.elapsed_ms = (monotonic_ns() - start) / 1000000.0;
    if (reuse_fd < 0) close(fd);
    return result;
}

static txn_result_t do_transaction(const options_t *opt, const target_t *target, int reuse_fd) {
    txn_result_t final;
    memset(&final, 0, sizeof(final));
    int attempts = opt->extra_retry + 1;
    final.ok_attempt = 0;
    char first_status[32] = "";
    char status_list[256] = "";
    for (int i = 1; i <= attempts; i++) {
        txn_result_t item = do_attempt(opt, target, reuse_fd);
        if (i == 1) snprintf(first_status, sizeof(first_status), "%s", item.status);
        if (status_list[0]) strncat(status_list, ",", sizeof(status_list) - strlen(status_list) - 1);
        strncat(status_list, item.status, sizeof(status_list) - strlen(status_list) - 1);
        final = item;
        if (strcmp(item.status, "ok") == 0) {
            final.ok_attempt = i;
            break;
        }
        if (i < attempts) sleep_ms(opt->retry_gap_ms);
    }
    final.attempts = final.ok_attempt ? final.ok_attempt : attempts;
    snprintf(final.first_status, sizeof(final.first_status), "%s", first_status);
    return final;
}

static void emit_sample(int seq, int cycle, const target_t *target, const txn_result_t *result) {
    printf("{\"type\":\"sample\",\"seq\":%d,\"cycle\":%d,\"target\":\"%s\",\"address\":%d,"
           "\"function\":%d,\"register\":%d,\"count\":%d,\"status\":\"%s\","
           "\"first_attempt_status\":\"%s\",\"attempts\":%d,\"ok_attempt\":%d,"
           "\"elapsed_ms\":%.3f,\"request_hex\":\"",
           seq, cycle, target->name, target->address, target->function, target->reg,
           target->count, result->status, result->first_status, result->attempts,
           result->ok_attempt, result->elapsed_ms);
    print_hex_json(result->request, 8);
    printf("\",\"rx_hex\":\"");
    print_hex_json(result->response, result->rx_len);
    printf("\",\"rx_len\":%d,\"expected_len\":%d}\n", result->rx_len, result->expected_len);
    fflush(stdout);
}

static const char **scenario_targets(const char *scenario, int *count) {
    static const char *env[] = {"env-temp", "env-humi", "env-smoke"};
    static const char *gas[] = {"co", "h2s", "o2", "ch4"};
    static const char *bms_fast[] = {"bms-voltage", "bms-current", "bms-soc", "bms-status"};
    static const char *prod[] = {"photo", "env-temp", "env-humi", "env-smoke", "co", "h2s", "o2", "ch4", "bms-voltage", "bms-current", "bms-soc", "bms-status"};
    if (strcmp(scenario, "env") == 0) { *count = 3; return env; }
    if (strcmp(scenario, "gas") == 0) { *count = 4; return gas; }
    if (strcmp(scenario, "bms-fast") == 0) { *count = 4; return bms_fast; }
    if (strcmp(scenario, "production-lite") == 0) { *count = 12; return prod; }
    *count = 1;
    return NULL;
}

static void usage(const char *argv0) {
    fprintf(stderr, "usage: %s [--port /dev/ttyS6] [--target env-temp] [--scenario env|gas|bms-fast|production-lite] [--samples N] [--cycles N] [--batch] [--gap-ms N] [--target-timeout-ms N] [--post-tx-delay-ms N] [--extra-retry N] [--rs485] [--keep-open]\n", argv0);
}

static void parse_args(int argc, char **argv, options_t *opt) {
    opt->port = "/dev/ttyS6";
    opt->baud = 9600;
    opt->samples = 20;
    opt->cycles = 10;
    opt->gap_ms = 200;
    opt->target_timeout_ms = -1;
    opt->post_tx_delay_ms = -1;
    opt->extra_retry = 0;
    opt->retry_gap_ms = 50;
    opt->batch = false;
    opt->rs485 = false;
    opt->keep_open = false;
    opt->targets_arg = "env-temp";
    opt->scenario = NULL;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--port") == 0 && i + 1 < argc) opt->port = argv[++i];
        else if (strcmp(argv[i], "--baud") == 0 && i + 1 < argc) opt->baud = atoi(argv[++i]);
        else if (strcmp(argv[i], "--target") == 0 && i + 1 < argc) opt->targets_arg = argv[++i];
        else if (strcmp(argv[i], "--scenario") == 0 && i + 1 < argc) { opt->scenario = argv[++i]; opt->batch = true; }
        else if (strcmp(argv[i], "--samples") == 0 && i + 1 < argc) opt->samples = atoi(argv[++i]);
        else if (strcmp(argv[i], "--cycles") == 0 && i + 1 < argc) opt->cycles = atoi(argv[++i]);
        else if (strcmp(argv[i], "--gap-ms") == 0 && i + 1 < argc) opt->gap_ms = atoi(argv[++i]);
        else if (strcmp(argv[i], "--target-timeout-ms") == 0 && i + 1 < argc) opt->target_timeout_ms = atoi(argv[++i]);
        else if (strcmp(argv[i], "--post-tx-delay-ms") == 0 && i + 1 < argc) opt->post_tx_delay_ms = atoi(argv[++i]);
        else if (strcmp(argv[i], "--extra-retry") == 0 && i + 1 < argc) opt->extra_retry = atoi(argv[++i]);
        else if (strcmp(argv[i], "--retry-gap-ms") == 0 && i + 1 < argc) opt->retry_gap_ms = atoi(argv[++i]);
        else if (strcmp(argv[i], "--batch") == 0) opt->batch = true;
        else if (strcmp(argv[i], "--rs485") == 0) opt->rs485 = true;
        else if (strcmp(argv[i], "--keep-open") == 0) opt->keep_open = true;
        else { usage(argv[0]); exit(2); }
    }
}

int main(int argc, char **argv) {
    options_t opt;
    parse_args(argc, argv, &opt);

    printf("{\"type\":\"run_meta\",\"tool\":\"craw\",\"mode\":\"%s\",\"port\":\"",
           opt.batch ? "batch" : "single");
    json_escape(opt.port);
    printf("\",\"baud\":%d,\"gap_ms\":%d,\"extra_retry\":%d,\"rs485\":%s,\"keep_open\":%s}\n",
           opt.baud, opt.gap_ms, opt.extra_retry, opt.rs485 ? "true" : "false",
           opt.keep_open ? "true" : "false");
    fflush(stdout);

    const char *single_list[1] = {opt.targets_arg};
    const char **names = single_list;
    int target_count = 1;
    if (opt.scenario) {
        names = scenario_targets(opt.scenario, &target_count);
        if (!names) {
            fprintf(stderr, "unknown scenario: %s\n", opt.scenario);
            return 2;
        }
    }

    int fd = -1;
    if (opt.keep_open) {
        fd = open_serial(&opt);
        if (fd < 0) {
            fprintf(stderr, "open failed: %s\n", strerror(errno));
            return 1;
        }
    }

    int total = 0, ok = 0, first_ok = 0;
    int seq = 0;
    int cycles = opt.batch ? opt.cycles : opt.samples;
    for (int cycle = 1; cycle <= cycles; cycle++) {
        for (int i = 0; i < target_count; i++) {
            const target_t *target = find_target(names[i]);
            if (!target) {
                fprintf(stderr, "unknown target: %s\n", names[i]);
                if (fd >= 0) close(fd);
                return 2;
            }
            txn_result_t result = do_transaction(&opt, target, fd);
            seq++;
            total++;
            if (strcmp(result.status, "ok") == 0) ok++;
            if (strcmp(result.first_status, "ok") == 0) first_ok++;
            emit_sample(seq, cycle, target, &result);
            sleep_ms(opt.gap_ms);
        }
    }
    if (fd >= 0) close(fd);

    printf("{\"type\":\"summary\",\"samples\":%d,\"ok\":%d,\"success_rate\":%.6f,"
           "\"first_attempt_ok\":%d,\"first_attempt_success_rate\":%.6f}\n",
           total, ok, total ? (double)ok / (double)total : 0.0,
           first_ok, total ? (double)first_ok / (double)total : 0.0);
    fflush(stdout);
    return 0;
}
