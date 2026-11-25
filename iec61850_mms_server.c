/* -*- coding: utf-8 -*-
 * Servidor IEC 61850 MMS (libiec61850) para Subestacao 230kV
 * Copyright (C) 2026 Patrick Araujo Santana
 * Licenca: GPLv3
 */

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif

#include <winsock2.h>
#include <ws2tcpip.h>

#include "iec61850_server.h"
#include "hal_thread.h"
#include "hal_time.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>
#include <stdint.h>
#include <errno.h>

#define UDP_BRIDGE_HOST   "127.0.0.1"
#define UDP_C_SERVER_PORT 50102
#define UDP_PY_CMD_PORT   50103

typedef struct {
    int pos_52l1;      /* 0 = INTERMEDIATE (DBPOS_INTERMEDIATE_STATE), 1 = OPEN (DBPOS_OFF), 2 = CLOSED (DBPOS_ON) */
    int sw_89bp;       /* 0 = OPEN, 1 = CLOSED */
    int sw_89t1;       /* 0 = OPEN, 1 = CLOSED */
    bool trip_81u;
    bool trip_81o;
    float hz;
    float kv;
    float amp;
    bool dirty;
    uint32_t ack_seq;
    bool ack_received;
    bool ack_accepted;
} SubstationState;

static SubstationState g_state = {
    .pos_52l1 = 2,
    .sw_89bp = 0,
    .sw_89t1 = 0,
    .trip_81u = false,
    .trip_81o = false,
    .hz = 60.0f,
    .kv = 230.0f,
    .amp = 180.0f,
    .dirty = true,
    .ack_seq = 0,
    .ack_received = false,
    .ack_accepted = false
};

static Semaphore g_state_lock = NULL;
static volatile bool g_running = true;
static IedServer g_ied_server = NULL;
static IedModel* g_ied_model = NULL;
static uint32_t g_cmd_seq = 0;

/* Nos do modelo dinamico */
static DataAttribute* g_da_xcbr1_pos      = NULL;
static DataAttribute* g_da_xcbr1_q        = NULL;
static DataAttribute* g_da_xcbr1_t        = NULL;
static DataAttribute* g_da_xcbr1_ctlmodel = NULL;
static DataAttribute* g_da_xswi1_pos      = NULL;
static DataAttribute* g_da_xswi2_pos      = NULL;
static DataAttribute* g_da_mmxu1_hz       = NULL;
static DataAttribute* g_da_mmxu1_v        = NULL;
static DataAttribute* g_da_mmxu1_a        = NULL;
static DataAttribute* g_da_ptuf1_op       = NULL;
static DataAttribute* g_da_ptof1_op       = NULL;

static SOCKET g_udp_sock = INVALID_SOCKET;
static struct sockaddr_in g_py_addr;

static bool send_operate_to_python(const char* bay, bool close_cmd, uint32_t seq)
{
    if (g_udp_sock == INVALID_SOCKET) {
        return false;
    }
    char buf[256];
    int len = snprintf(
        buf,
        sizeof(buf),
        "{\"cmd\":\"OPERATE\",\"bay\":\"%s\",\"close\":%s,\"ctlVal\":%d,\"seq\":%u}\n",
        bay ? bay : "52L1",
        close_cmd ? "true" : "false",
        close_cmd ? 2 : 1,
        (unsigned int)seq
    );
    if (len <= 0 || len >= (int)sizeof(buf)) {
        return false;
    }
    int sent = sendto(g_udp_sock, buf, len, 0, (struct sockaddr*)&g_py_addr, sizeof(g_py_addr));
    return (sent == len);
}

static bool wait_python_operate_confirm(uint32_t seq, bool close_cmd, int timeout_ms)
{
    int elapsed = 0;
    int target_pos = close_cmd ? 2 : 1;

    while (elapsed < timeout_ms && g_running) {
        Semaphore_wait(g_state_lock);
        bool got_ack = (g_state.ack_received && g_state.ack_seq == seq);
        bool accepted = g_state.ack_accepted;
        int current_pos = g_state.pos_52l1;
        Semaphore_post(g_state_lock);

        if (got_ack) {
            if (!accepted) {
                return false;
            }
            if (current_pos == target_pos) {
                return true;
            }
        }

        Thread_sleep(20);
        elapsed += 20;
    }

    return false;
}

static ControlHandlerResult control_handler_xcbr1(ControlAction action, void* parameter, MmsValue* value, bool test)
{
    (void)action;
    (void)parameter;
    (void)test;

    if (!value || MmsValue_getType(value) != MMS_BIT_STRING) {
        return CONTROL_RESULT_FAILED;
    }

    if (MmsValue_getBitStringSize(value) != 2) {
        return CONTROL_RESULT_FAILED;
    }

    uint32_t raw = MmsValue_getBitStringAsIntegerBigEndian(value);
    bool close_cmd = false;

    if (raw == 1) {
        close_cmd = false;
    } else if (raw == 2) {
        close_cmd = true;
    } else {
        return CONTROL_RESULT_FAILED;
    }

    uint32_t seq = ++g_cmd_seq;

    Semaphore_wait(g_state_lock);
    g_state.ack_seq = seq;
    g_state.ack_received = false;
    g_state.ack_accepted = false;
    Semaphore_post(g_state_lock);

    if (!send_operate_to_python("52L1", close_cmd, seq)) {
        return CONTROL_RESULT_FAILED;
    }

    if (!wait_python_operate_confirm(seq, close_cmd, 1500)) {
        return CONTROL_RESULT_FAILED;
    }

    return CONTROL_RESULT_OK;
}

static CheckHandlerResult check_handler_xcbr1(ControlAction action, void* parameter, MmsValue* ctlVal, bool test, bool interlockCheck)
{
    (void)action;
    (void)parameter;
    (void)test;

    if (interlockCheck) {
        bool close_requested = false;
        if (ctlVal && MmsValue_getType(ctlVal) == MMS_BIT_STRING && MmsValue_getBitStringSize(ctlVal) == 2) {
            close_requested = (MmsValue_getBitStringAsIntegerBigEndian(ctlVal) == 2);
        } else if (ctlVal && MmsValue_getType(ctlVal) == MMS_BOOLEAN) {
            close_requested = MmsValue_getBoolean(ctlVal);
        }

        Semaphore_wait(g_state_lock);
        int bp = g_state.sw_89bp;
        int t1 = g_state.sw_89t1;
        Semaphore_post(g_state_lock);

        if (close_requested && t1 != 0) {
            return CONTROL_OBJECT_ACCESS_DENIED;
        }
        if (close_requested && bp != 0 && t1 != 0) {
            return CONTROL_OBJECT_ACCESS_DENIED;
        }
    }

    return CONTROL_ACCEPTED;
}

static bool parse_json_int(const char* json, const char* key, int* out_val)
{
    char pattern[64];
    snprintf(pattern, sizeof(pattern), "\"%s\":", key);
    const char* p = strstr(json, pattern);
    if (!p) {
        return false;
    }
    p += strlen(pattern);
    while (*p == ' ' || *p == '\t') {
        p++;
    }
    if (*p == '\0') {
        return false;
    }
    char* endptr = NULL;
    errno = 0;
    long v = strtol(p, &endptr, 10);
    if (endptr == p || errno == ERANGE) {
        return false;
    }
    *out_val = (int)v;
    return true;
}

static bool parse_json_uint(const char* json, const char* key, uint32_t* out_val)
{
    char pattern[64];
    snprintf(pattern, sizeof(pattern), "\"%s\":", key);
    const char* p = strstr(json, pattern);
    if (!p) {
        return false;
    }
    p += strlen(pattern);
    while (*p == ' ' || *p == '\t') {
        p++;
    }
    if (*p == '\0') {
        return false;
    }
    char* endptr = NULL;
    errno = 0;
    unsigned long v = strtoul(p, &endptr, 10);
    if (endptr == p || errno == ERANGE) {
        return false;
    }
    *out_val = (uint32_t)v;
    return true;
}

static bool parse_json_float(const char* json, const char* key, float* out_val)
{
    char pattern[64];
    snprintf(pattern, sizeof(pattern), "\"%s\":", key);
    const char* p = strstr(json, pattern);
    if (!p) {
        return false;
    }
    p += strlen(pattern);
    while (*p == ' ' || *p == '\t') {
        p++;
    }
    if (*p == '\0') {
        return false;
    }
    char* endptr = NULL;
    errno = 0;
    float parsed = strtof(p, &endptr);
    if (endptr == p || errno == ERANGE) {
        return false;
    }
    *out_val = parsed;
    return true;
}

static void apply_json_update(const char* line)
{
    if (!line || !*line) {
        return;
    }

    int iv = 0;
    float fv = 0.0f;
    uint32_t seq = 0;

    Semaphore_wait(g_state_lock);

    if (strstr(line, "\"ack\":") != NULL) {
        printf("[UDP-ACK] %s\n", line);
        fflush(stdout);
        bool accepted = true;
        if (strstr(line, "\"ack\":false") != NULL || strstr(line, "\"ok\":false") != NULL || strstr(line, "\"status\":\"rejected\"") != NULL) {
            accepted = false;
        }
        if (parse_json_uint(line, "seq", &seq)) {
            g_state.ack_seq = seq;
        } else {
            g_state.ack_seq = g_cmd_seq;
        }
        g_state.ack_received = true;
        g_state.ack_accepted = accepted;
    }

    if (parse_json_int(line, "52L1", &iv)) {
        if (iv == 2) {
            g_state.pos_52l1 = 2;
        } else if (iv == 0) {
            g_state.pos_52l1 = 0;
        } else {
            g_state.pos_52l1 = 1;
        }
        g_state.dirty = true;
    }
    if (parse_json_int(line, "89BP", &iv)) {
        g_state.sw_89bp = (iv != 0) ? 1 : 0;
        g_state.dirty = true;
    }
    if (parse_json_int(line, "89T1", &iv)) {
        g_state.sw_89t1 = (iv != 0) ? 1 : 0;
        g_state.dirty = true;
    }
    if (parse_json_int(line, "81U", &iv)) {
        g_state.trip_81u = (iv != 0);
        g_state.dirty = true;
    }
    if (parse_json_int(line, "81O", &iv)) {
        g_state.trip_81o = (iv != 0);
        g_state.dirty = true;
    }
    if (parse_json_float(line, "hz", &fv)) {
        g_state.hz = fv;
        g_state.dirty = true;
    }
    if (parse_json_float(line, "kv", &fv)) {
        g_state.kv = fv;
        g_state.dirty = true;
    }
    if (parse_json_float(line, "amp", &fv)) {
        g_state.amp = fv;
        g_state.dirty = true;
    }

    Semaphore_post(g_state_lock);
}

static void* udp_bridge_thread(void* arg)
{
    (void)arg;
    char recv_buf[2048];
    char line_acc[4096];
    size_t acc_len = 0;

    while (g_running) {
        struct sockaddr_in from_addr;
        int from_len = sizeof(from_addr);
        int n = recvfrom(g_udp_sock, recv_buf, (int)sizeof(recv_buf) - 1, 0, (struct sockaddr*)&from_addr, &from_len);
        if (n <= 0) {
            int err = WSAGetLastError();
            if (err == WSAETIMEDOUT || err == WSAEINTR) {
                continue;
            }
            Thread_sleep(50);
            continue;
        }
        recv_buf[n] = '\0';

        for (int i = 0; i < n; i++) {
            char ch = recv_buf[i];
            if (ch == '\n' || ch == '\r') {
                if (acc_len > 0) {
                    line_acc[acc_len] = '\0';
                    apply_json_update(line_acc);
                    acc_len = 0;
                }
            } else if (acc_len + 1 < sizeof(line_acc)) {
                line_acc[acc_len++] = ch;
            } else {
                acc_len = 0;
            }
        }
    }

    if (acc_len > 0) {
        line_acc[acc_len] = '\0';
        apply_json_update(line_acc);
    }
    return NULL;
}

static void create_dpc_control_structure(DataObject* pos_do)
{
    DataObject* oper = DataObject_create("Oper", (ModelNode*)pos_do, 0);
    DataAttribute_create("ctlVal", (ModelNode*)oper, IEC61850_CODEDENUM, IEC61850_FC_CO, 0, 0, 0);

    DataObject* origin = DataObject_create("origin", (ModelNode*)oper, 0);
    DataAttribute_create("orCat", (ModelNode*)origin, IEC61850_ENUMERATED, IEC61850_FC_CO, 0, 0, 0);
    DataAttribute_create("orIdent", (ModelNode*)origin, IEC61850_OCTET_STRING_64, IEC61850_FC_CO, 0, 0, 0);

    DataAttribute_create("ctlNum", (ModelNode*)oper, IEC61850_INT8U, IEC61850_FC_CO, 0, 0, 0);
    DataAttribute_create("T", (ModelNode*)oper, IEC61850_TIMESTAMP, IEC61850_FC_CO, 0, 0, 0);
    DataAttribute_create("Test", (ModelNode*)oper, IEC61850_BOOLEAN, IEC61850_FC_CO, 0, 0, 0);
    DataAttribute_create("Check", (ModelNode*)oper, IEC61850_CHECK, IEC61850_FC_CO, 0, 0, 0);
}

static DataAttribute* create_analog_mag_f(LogicalNode* ln, const char* do_name)
{
    DataObject* analog_do = DataObject_create(do_name, (ModelNode*)ln, 0);
    DataObject* mag_do = DataObject_create("mag", (ModelNode*)analog_do, 0);
    return DataAttribute_create("f", (ModelNode*)mag_do, IEC61850_FLOAT32, IEC61850_FC_MX, 0, 0, 0);
}

static IedModel* build_substation_model(void)
{
    IedModel* model = IedModel_create("E230kV_Substation");
    LogicalDevice* ld = LogicalDevice_create("Bay_L1_TR1", model);

    LogicalNode_create("LLN0", ld);

    LogicalNode* xcbr1 = LogicalNode_create("XCBR1", ld);
    DataObject* xcbr1_pos = DataObject_create("Pos", (ModelNode*)xcbr1, 0);
    g_da_xcbr1_pos = DataAttribute_create("stVal", (ModelNode*)xcbr1_pos, IEC61850_CODEDENUM, IEC61850_FC_ST, 0, 0, 0);
    g_da_xcbr1_q = DataAttribute_create("q", (ModelNode*)xcbr1_pos, IEC61850_QUALITY, IEC61850_FC_ST, 0, 0, 0);
    g_da_xcbr1_t = DataAttribute_create("t", (ModelNode*)xcbr1_pos, IEC61850_TIMESTAMP, IEC61850_FC_ST, 0, 0, 0);
    g_da_xcbr1_ctlmodel = DataAttribute_create("ctlModel", (ModelNode*)xcbr1_pos, IEC61850_ENUMERATED, IEC61850_FC_CF, 0, 0, 0);
    create_dpc_control_structure(xcbr1_pos);

    LogicalNode* xswi1 = LogicalNode_create("XSWI1_BP", ld);
    DataObject* xswi1_pos = DataObject_create("Pos", (ModelNode*)xswi1, 0);
    g_da_xswi1_pos = DataAttribute_create("stVal", (ModelNode*)xswi1_pos, IEC61850_CODEDENUM, IEC61850_FC_ST, 0, 0, 0);

    LogicalNode* xswi2 = LogicalNode_create("XSWI2_T1", ld);
    DataObject* xswi2_pos = DataObject_create("Pos", (ModelNode*)xswi2, 0);
    g_da_xswi2_pos = DataAttribute_create("stVal", (ModelNode*)xswi2_pos, IEC61850_CODEDENUM, IEC61850_FC_ST, 0, 0, 0);

    LogicalNode* mmxu1 = LogicalNode_create("MMXU1", ld);
    g_da_mmxu1_hz = create_analog_mag_f(mmxu1, "Hz");
    g_da_mmxu1_v  = create_analog_mag_f(mmxu1, "V");
    g_da_mmxu1_a  = create_analog_mag_f(mmxu1, "A");

    LogicalNode* ptuf1 = LogicalNode_create("PTUF1", ld);
    DataObject* ptuf1_op = DataObject_create("Op", (ModelNode*)ptuf1, 0);
    g_da_ptuf1_op = DataAttribute_create("general", (ModelNode*)ptuf1_op, IEC61850_BOOLEAN, IEC61850_FC_ST, 0, 0, 0);

    LogicalNode* ptof1 = LogicalNode_create("PTOF1", ld);
    DataObject* ptof1_op = DataObject_create("Op", (ModelNode*)ptof1, 0);
    g_da_ptof1_op = DataAttribute_create("general", (ModelNode*)ptof1_op, IEC61850_BOOLEAN, IEC61850_FC_ST, 0, 0, 0);

    return model;
}

static void sync_model_values(IedServer server)
{
    Semaphore_wait(g_state_lock);
    if (!g_state.dirty) {
        Semaphore_post(g_state_lock);
        return;
    }

    int pos_52l1 = g_state.pos_52l1;
    bool bp_closed = (g_state.sw_89bp != 0);
    bool t1_closed = (g_state.sw_89t1 != 0);
    float hz = g_state.hz;
    float kv = g_state.kv;
    float amp = g_state.amp;
    bool u81 = g_state.trip_81u;
    bool o81 = g_state.trip_81o;
    g_state.dirty = false;
    Semaphore_post(g_state_lock);

    uint64_t now_ms = Hal_getTimeInMs();
    Timestamp ts;
    Timestamp_clearFlags(&ts);
    Timestamp_setTimeInMilliseconds(&ts, now_ms);

    Quality q = QUALITY_VALIDITY_GOOD;

    IedServer_lockDataModel(server);

    if (g_da_xcbr1_pos) {
        Dbpos db_pos = DBPOS_OFF;
        if (pos_52l1 == 2) {
            db_pos = DBPOS_ON;
        } else if (pos_52l1 == 0) {
            db_pos = DBPOS_INTERMEDIATE_STATE;
        }
        IedServer_updateDbposValue(server, g_da_xcbr1_pos, db_pos);
    }
    if (g_da_xcbr1_q) {
        IedServer_updateQuality(server, g_da_xcbr1_q, q);
    }
    if (g_da_xcbr1_t) {
        IedServer_updateTimestampAttributeValue(server, g_da_xcbr1_t, &ts);
    }
    if (g_da_xswi1_pos) {
        IedServer_updateDbposValue(server, g_da_xswi1_pos, bp_closed ? DBPOS_ON : DBPOS_OFF);
    }
    if (g_da_xswi2_pos) {
        IedServer_updateDbposValue(server, g_da_xswi2_pos, t1_closed ? DBPOS_ON : DBPOS_OFF);
    }
    if (g_da_mmxu1_hz) {
        IedServer_updateFloatAttributeValue(server, g_da_mmxu1_hz, hz);
    }
    if (g_da_mmxu1_v) {
        IedServer_updateFloatAttributeValue(server, g_da_mmxu1_v, kv);
    }
    if (g_da_mmxu1_a) {
        IedServer_updateFloatAttributeValue(server, g_da_mmxu1_a, amp);
    }
    if (g_da_ptuf1_op) {
        IedServer_updateBooleanAttributeValue(server, g_da_ptuf1_op, u81);
    }
    if (g_da_ptof1_op) {
        IedServer_updateBooleanAttributeValue(server, g_da_ptof1_op, o81);
    }

    IedServer_unlockDataModel(server);
}

static bool configure_and_start_server(IedServer server, const char* ip, int port)
{
    if (!server) {
        return false;
    }

    DataObject* xcbr1_pos = (DataObject*)IedModel_getModelNodeByObjectReference(
        g_ied_model, "E230kV_SubstationBay_L1_TR1/XCBR1.Pos"
    );
    if (!xcbr1_pos) {
        fprintf(stderr, "[ERRO] Objeto XCBR1.Pos nao encontrado no modelo IEC 61850.\n");
        return false;
    }

    IedServer_updateCtlModel(server, xcbr1_pos, CONTROL_MODEL_DIRECT_NORMAL);
    IedServer_setPerformCheckHandler(server, xcbr1_pos, check_handler_xcbr1, NULL);
    IedServer_setControlHandler(server, xcbr1_pos, control_handler_xcbr1, NULL);

    if (ip && ip[0] != '\0' && strcmp(ip, "0.0.0.0") != 0) {
        IedServer_setLocalIpAddress(server, ip);
    }

    Semaphore_wait(g_state_lock);
    g_state.dirty = true;
    Semaphore_post(g_state_lock);
    sync_model_values(server);

    IedServer_start(server, port);
    return IedServer_isRunning(server);
}

int main(int argc, char** argv)
{
    const char* bind_ip = (argc > 1 && argv[1][0] != '\0') ? argv[1] : "0.0.0.0";
    const char* active_ip = bind_ip;
    int active_port = 102;

    WSADATA wsa_data;
    if (WSAStartup(MAKEWORD(2, 2), &wsa_data) != 0) {
        fprintf(stderr, "Falha ao inicializar Winsock.\n");
        return 1;
    }

    g_state_lock = Semaphore_create(1);

    g_udp_sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (g_udp_sock != INVALID_SOCKET) {
        BOOL reuse = TRUE;
        setsockopt(g_udp_sock, SOL_SOCKET, SO_REUSEADDR, (const char*)&reuse, sizeof(reuse));
        DWORD timeout_ms = 250;
        setsockopt(g_udp_sock, SOL_SOCKET, SO_RCVTIMEO, (const char*)&timeout_ms, sizeof(timeout_ms));

        struct sockaddr_in local_addr;
        memset(&local_addr, 0, sizeof(local_addr));
        local_addr.sin_family = AF_INET;
        local_addr.sin_port = htons(UDP_C_SERVER_PORT);
        inet_pton(AF_INET, UDP_BRIDGE_HOST, &local_addr.sin_addr);

        if (bind(g_udp_sock, (struct sockaddr*)&local_addr, sizeof(local_addr)) == SOCKET_ERROR) {
            closesocket(g_udp_sock);
            g_udp_sock = INVALID_SOCKET;
        }
    }

    memset(&g_py_addr, 0, sizeof(g_py_addr));
    g_py_addr.sin_family = AF_INET;
    g_py_addr.sin_port = htons(UDP_PY_CMD_PORT);
    inet_pton(AF_INET, UDP_BRIDGE_HOST, &g_py_addr.sin_addr);

    g_ied_model = build_substation_model();
    g_ied_server = IedServer_create(g_ied_model);
    if (!g_ied_server) {
        fprintf(stderr, "Falha ao criar IedServer.\n");
        IedModel_destroy(g_ied_model);
        Semaphore_destroy(g_state_lock);
        WSACleanup();
        return 1;
    }

    if (!configure_and_start_server(g_ied_server, bind_ip, 102)) {
        IedServer_stop(g_ied_server);
        IedServer_destroy(g_ied_server);
        g_ied_server = NULL;

        g_ied_server = IedServer_create(g_ied_model);
        active_ip = "0.0.0.0";
        active_port = 10102;
        if (!configure_and_start_server(g_ied_server, active_ip, active_port)) {
            fprintf(stderr, "Falha ao iniciar IedServer nas portas 102 e 10102.\n");
            if (g_ied_server) {
                IedServer_destroy(g_ied_server);
            }
            IedModel_destroy(g_ied_model);
            Semaphore_destroy(g_state_lock);
            WSACleanup();
            return 1;
        }
    }

    printf("Servidor IEC 61850 MMS iniciado (bind=%s, porta=%d).\n", active_ip, active_port);
    fflush(stdout);

    Thread udp_th = NULL;
    if (g_udp_sock != INVALID_SOCKET) {
        udp_th = Thread_create(udp_bridge_thread, NULL, false);
        Thread_start(udp_th);
    }

    while (g_running && IedServer_isRunning(g_ied_server)) {
        sync_model_values(g_ied_server);
        Thread_sleep(100);
    }

    g_running = false;
    if (udp_th) {
        Thread_sleep(300);
        Thread_destroy(udp_th);
    }
    if (g_udp_sock != INVALID_SOCKET) {
        closesocket(g_udp_sock);
        g_udp_sock = INVALID_SOCKET;
    }

    if (g_ied_server) {
        IedServer_stop(g_ied_server);
        IedServer_destroy(g_ied_server);
    }
    IedModel_destroy(g_ied_model);
    Semaphore_destroy(g_state_lock);
    WSACleanup();
    return 0;
}