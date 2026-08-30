<?php
declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store, no-cache, must-revalidate');

$configFile = __DIR__ . '/config.php';
if (!is_file($configFile)) {
    http_response_code(503);
    echo json_encode(['ok' => false, 'error' => 'backend_not_configured']);
    exit;
}
$config = require $configFile;

$origin = $_SERVER['HTTP_ORIGIN'] ?? '';
$allowed = $config['allowed_origins'] ?? [];
if ($origin !== '' && in_array($origin, $allowed, true)) {
    header('Access-Control-Allow-Origin: ' . $origin);
    header('Vary: Origin');
}
header('Access-Control-Allow-Headers: Content-Type, Authorization, X-7Sky-Key');
header('Access-Control-Allow-Methods: GET, POST, PUT, DELETE, OPTIONS');
if (($_SERVER['REQUEST_METHOD'] ?? 'GET') === 'OPTIONS') {
    http_response_code(204);
    exit;
}

function out(array $data, int $status = 200): never {
    http_response_code($status);
    echo json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

function body(): array {
    $raw = file_get_contents('php://input');
    if ($raw === false || trim($raw) === '') return [];
    $data = json_decode($raw, true);
    if (!is_array($data)) out(['ok' => false, 'error' => 'invalid_json'], 400);
    return $data;
}

function bearer(): string {
    $h = $_SERVER['HTTP_AUTHORIZATION'] ?? '';
    if (preg_match('/^Bearer\s+(.+)$/i', $h, $m)) return trim($m[1]);
    return trim($_SERVER['HTTP_X_7SKY_KEY'] ?? '');
}

function requireAuth(array $config): void {
    $expected = (string)($config['api_token'] ?? '');
    $actual = bearer();
    if ($expected === '' || !hash_equals($expected, $actual)) {
        out(['ok' => false, 'error' => 'unauthorized'], 401);
    }
}

function db(array $config): PDO {
    static $pdo = null;
    if ($pdo instanceof PDO) return $pdo;
    $dsn = 'mysql:host=' . $config['db_host'] . ';dbname=' . $config['db_name'] . ';charset=utf8mb4';
    $pdo = new PDO($dsn, $config['db_user'], $config['db_pass'], [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        PDO::ATTR_EMULATE_PREPARES => false,
    ]);
    return $pdo;
}

function payload(array $row): array {
    $p = json_decode((string)($row['payload'] ?? '{}'), true);
    return is_array($p) ? $p : [];
}

$resource = strtolower(trim((string)($_GET['resource'] ?? 'health')));
$method = strtoupper($_SERVER['REQUEST_METHOD'] ?? 'GET');

try {
    if ($resource === 'health') {
        $pdo = db($config);
        $pdo->query('SELECT 1');
        out(['ok' => true, 'service' => 'seventh-sky-command-center-api', 'database' => 'connected', 'time' => gmdate('c')]);
    }

    requireAuth($config);
    $pdo = db($config);

    if ($resource === 'bootstrap' && $method === 'POST') {
        $d = body();
        $items = $d['leads'] ?? [];
        if (!is_array($items)) out(['ok' => false, 'error' => 'leads_must_be_array'], 400);
        $pdo->beginTransaction();
        $q = $pdo->prepare('INSERT INTO leads (id,payload) VALUES (?,?) ON DUPLICATE KEY UPDATE payload=VALUES(payload), updated_at=CURRENT_TIMESTAMP');
        $count = 0;
        foreach ($items as $lead) {
            if (!is_array($lead) || empty($lead['id'])) continue;
            $q->execute([(string)$lead['id'], json_encode($lead, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES)]);
            $count++;
        }
        $pdo->commit();
        out(['ok' => true, 'imported' => $count]);
    }

    if ($resource === 'leads' && $method === 'GET') {
        $rows = $pdo->query('SELECT id,payload,updated_at FROM leads ORDER BY updated_at DESC')->fetchAll();
        $items = [];
        foreach ($rows as $r) {
            $p = payload($r);
            if (!isset($p['id'])) $p['id'] = $r['id'];
            $p['_serverUpdatedAt'] = $r['updated_at'];
            $items[] = $p;
        }
        out(['ok' => true, 'leads' => $items]);
    }

    if ($resource === 'state') {
        $leadId = trim((string)($_GET['lead_id'] ?? ''));
        if ($leadId === '') out(['ok' => false, 'error' => 'lead_id_required'], 400);
        if ($method === 'GET') {
            $q = $pdo->prepare('SELECT payload,updated_at FROM crm_state WHERE lead_id=?');
            $q->execute([$leadId]);
            $r = $q->fetch();
            out(['ok' => true, 'state' => $r ? payload($r) : null, 'updatedAt' => $r['updated_at'] ?? null]);
        }
        if ($method === 'PUT' || $method === 'POST') {
            $d = body();
            $q = $pdo->prepare('INSERT INTO crm_state (lead_id,payload) VALUES (?,?) ON DUPLICATE KEY UPDATE payload=VALUES(payload), updated_at=CURRENT_TIMESTAMP');
            $q->execute([$leadId, json_encode($d, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES)]);
            out(['ok' => true]);
        }
    }

    if ($resource === 'activities') {
        $leadId = trim((string)($_GET['lead_id'] ?? ''));
        if ($leadId === '') out(['ok' => false, 'error' => 'lead_id_required'], 400);
        if ($method === 'GET') {
            $q = $pdo->prepare('SELECT id,kind,payload,created_at FROM activities WHERE lead_id=? ORDER BY id DESC LIMIT 200');
            $q->execute([$leadId]);
            $items = [];
            foreach ($q->fetchAll() as $r) $items[] = ['id'=>$r['id'],'kind'=>$r['kind'],'data'=>payload($r),'createdAt'=>$r['created_at']];
            out(['ok' => true, 'activities' => $items]);
        }
        if ($method === 'POST') {
            $d = body();
            $kind = trim((string)($d['kind'] ?? 'event'));
            $data = is_array($d['data'] ?? null) ? $d['data'] : $d;
            $q = $pdo->prepare('INSERT INTO activities (lead_id,kind,payload) VALUES (?,?,?)');
            $q->execute([$leadId, $kind, json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES)]);
            out(['ok' => true, 'id' => $pdo->lastInsertId()]);
        }
    }

    if ($resource === 'pricing') {
        $leadId = trim((string)($_GET['lead_id'] ?? ''));
        if ($leadId === '') out(['ok' => false, 'error' => 'lead_id_required'], 400);
        if ($method === 'GET') {
            $q = $pdo->prepare('SELECT id,payload,created_at FROM pricing_snapshots WHERE lead_id=? ORDER BY id DESC LIMIT 20');
            $q->execute([$leadId]);
            $items=[]; foreach($q->fetchAll() as $r) $items[]=['id'=>$r['id'],'data'=>payload($r),'createdAt'=>$r['created_at']];
            out(['ok'=>true,'pricing'=>$items]);
        }
        if ($method === 'POST') {
            $d = body();
            $q = $pdo->prepare('INSERT INTO pricing_snapshots (lead_id,payload) VALUES (?,?)');
            $q->execute([$leadId, json_encode($d, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES)]);
            out(['ok'=>true,'id'=>$pdo->lastInsertId()]);
        }
    }

    if ($resource === 'inbox') {
        if ($method === 'GET') {
            $limit = max(1, min(100, (int)($_GET['limit'] ?? 30)));
            $q = $pdo->prepare('SELECT message_key,lead_id,payload,received_at FROM inbox_events ORDER BY received_at DESC LIMIT ' . $limit);
            $q->execute();
            $items=[]; foreach($q->fetchAll() as $r) $items[]=['messageKey'=>$r['message_key'],'leadId'=>$r['lead_id'],'data'=>payload($r),'receivedAt'=>$r['received_at']];
            out(['ok'=>true,'events'=>$items]);
        }
        if ($method === 'POST') {
            $d = body();
            $messageKey = trim((string)($d['messageKey'] ?? $d['id'] ?? ''));
            if ($messageKey === '') out(['ok'=>false,'error'=>'message_key_required'],400);
            $leadId = isset($d['leadId']) ? trim((string)$d['leadId']) : null;
            $q = $pdo->prepare('INSERT INTO inbox_events (message_key,lead_id,payload) VALUES (?,?,?) ON DUPLICATE KEY UPDATE lead_id=VALUES(lead_id), payload=VALUES(payload)');
            $q->execute([$messageKey, $leadId ?: null, json_encode($d, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES)]);
            out(['ok'=>true]);
        }
    }

    if ($resource === 'push-subscriptions' && $method === 'POST') {
        $d = body();
        $endpoint = trim((string)($d['endpoint'] ?? ''));
        if ($endpoint === '') out(['ok'=>false,'error'=>'endpoint_required'],400);
        $hash = hash('sha256', $endpoint);
        $q = $pdo->prepare('INSERT INTO push_subscriptions (endpoint_hash,payload) VALUES (?,?) ON DUPLICATE KEY UPDATE payload=VALUES(payload), updated_at=CURRENT_TIMESTAMP');
        $q->execute([$hash, json_encode($d, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES)]);
        out(['ok'=>true]);
    }

    if ($resource === 'suppliers') {
        if ($method === 'GET') {
            $rows=$pdo->query('SELECT id,payload,updated_at FROM suppliers ORDER BY updated_at DESC')->fetchAll();
            $items=[]; foreach($rows as $r){$p=payload($r);$p['_id']=$r['id'];$p['_serverUpdatedAt']=$r['updated_at'];$items[]=$p;}
            out(['ok'=>true,'suppliers'=>$items]);
        }
        if ($method === 'POST' || $method === 'PUT') {
            $d=body(); $id=trim((string)($d['id']??'')); if($id==='') out(['ok'=>false,'error'=>'id_required'],400);
            $q=$pdo->prepare('INSERT INTO suppliers (id,payload) VALUES (?,?) ON DUPLICATE KEY UPDATE payload=VALUES(payload), updated_at=CURRENT_TIMESTAMP');
            $q->execute([$id,json_encode($d,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)]);
            out(['ok'=>true]);
        }
    }

    out(['ok' => false, 'error' => 'not_found'], 404);
} catch (Throwable $e) {
    if (isset($pdo) && $pdo instanceof PDO && $pdo->inTransaction()) $pdo->rollBack();
    error_log('7sky API: ' . $e->getMessage());
    out(['ok' => false, 'error' => 'server_error'], 500);
}
