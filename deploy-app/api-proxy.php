<?php
declare(strict_types=1);
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store, no-cache, must-revalidate');

$configFile = __DIR__ . '/app-api-config.php';
if (!is_file($configFile)) {
    http_response_code(503);
    echo json_encode(['ok'=>false,'error'=>'app_proxy_not_configured']);
    exit;
}
$config = require $configFile;
$backend = rtrim((string)($config['backend_base'] ?? ''), '/');
$token = (string)($config['api_token'] ?? '');
if ($backend === '' || $token === '') {
    http_response_code(503);
    echo json_encode(['ok'=>false,'error'=>'app_proxy_not_configured']);
    exit;
}

$resource = trim((string)($_GET['resource'] ?? 'health'));
$allowed = ['health','bootstrap','leads','state','activities','pricing','inbox','push-subscriptions','suppliers'];
if (!in_array($resource, $allowed, true)) {
    http_response_code(404);
    echo json_encode(['ok'=>false,'error'=>'resource_not_allowed']);
    exit;
}

$query = $_GET;
$query['resource'] = $resource;
$url = $backend . '/index.php?' . http_build_query($query);
$method = strtoupper($_SERVER['REQUEST_METHOD'] ?? 'GET');
$body = file_get_contents('php://input');

$ch = curl_init($url);
$headers = ['Accept: application/json','Authorization: Bearer ' . $token];
if (in_array($method, ['POST','PUT','PATCH','DELETE'], true)) {
    $headers[] = 'Content-Type: application/json';
    curl_setopt($ch, CURLOPT_POSTFIELDS, $body === false ? '' : $body);
}
curl_setopt_array($ch, [
    CURLOPT_CUSTOMREQUEST => $method,
    CURLOPT_HTTPHEADER => $headers,
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_FOLLOWLOCATION => false,
    CURLOPT_CONNECTTIMEOUT => 10,
    CURLOPT_TIMEOUT => 25,
]);
$response = curl_exec($ch);
$status = (int)curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
$error = curl_error($ch);
curl_close($ch);
if ($response === false) {
    http_response_code(502);
    echo json_encode(['ok'=>false,'error'=>'backend_proxy_failed','detail'=>$error]);
    exit;
}
http_response_code($status > 0 ? $status : 502);
echo $response;
