<?php
// POST /inventory/api/items.php  -- add one scanned bag.
// Auth: X-API-Key header (laptop scanner). Deliberately does NOT use auth.php.
//   201 {"id": N}               added
//   409 {"id": N, "error": ...} this bag (label_hash) is already in the inventory
//   400/401/405/422             bad JSON / bad key / not POST / rejected by the database rules
require __DIR__ . '/../db.php';

header('Content-Type: application/json');

function reply(int $code, array $body) {
    http_response_code($code);
    echo json_encode($body);
    exit;
}

if (!hash_equals(config()['api_key'], $_SERVER['HTTP_X_API_KEY'] ?? '')) {
    reply(401, ['error' => 'bad or missing API key']);
}
if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    reply(405, ['error' => 'POST only']);
}
$in = json_decode(file_get_contents('php://input'), true);
if (!is_array($in)) {
    reply(400, ['error' => 'body must be a JSON object']);
}

// Only these keys are stored; anything else in the request is ignored.
$COLUMNS = ['distributor', 'distributor_pn', 'mpn', 'manufacturer', 'type', 'mount', 'package',
            'value_num', 'value_text', 'tolerance', 'voltage_rating', 'power_rating', 'dielectric',
            'name', 'description', 'location', 'lot', 'date_code', 'original_qty', 'qty_remaining',
            'digikey_url', 'datasheet_url', 'label_hash', 'label_json', 'params_json', 'dk_raw_json'];

$row = [];
foreach ($COLUMNS as $c) {
    $v = $in[$c] ?? null;
    if (is_array($v)) $v = json_encode($v, JSON_UNESCAPED_UNICODE);
    if (is_string($v) && trim($v) === '') $v = null;
    $row[$c] = $v;
}
foreach (['type', 'mount', 'package', 'distributor'] as $c) {
    if ($row[$c] !== null) $row[$c] = strtoupper(trim($row[$c]));
}
$row['type'] = $row['type'] ?? 'OTHER';
$row['value_num'] = is_numeric($row['value_num']) ? (float)sprintf('%.6g', $row['value_num']) : null;
$row['original_qty'] = is_numeric($row['original_qty']) ? (int)$row['original_qty'] : null;

if (!is_numeric($in['qty_remaining'] ?? null) || (int)$in['qty_remaining'] < 0) {
    reply(422, ['error' => 'qty_remaining must be a whole number >= 0']);
}
$row['qty_remaining'] = (int)$in['qty_remaining'];
$member = trim((string)($in['member'] ?? ''));
if ($member === '') {
    reply(422, ['error' => 'member (who is adding this) is required']);
}

$db = null;
try {
    $db = db();
    $db->beginTransaction();
    $cols = implode(', ', $COLUMNS);
    $marks = implode(', ', array_map(fn($c) => ":$c", $COLUMNS));
    $db->prepare("INSERT INTO items ($cols) VALUES ($marks)")->execute($row);
    $id = (int)$db->lastInsertId();
    $db->prepare("INSERT INTO qty_log (item_id, old_qty, new_qty, member, source)
                  VALUES (?, 0, ?, ?, 'SCANNER')")->execute([$id, $row['qty_remaining'], $member]);
    $db->commit();
    reply(201, ['id' => $id]);
} catch (PDOException $e) {
    if ($db && $db->inTransaction()) $db->rollBack();
    $msg = $e->getMessage();
    if ($db && strpos($msg, 'items.label_hash') !== false) {
        $q = $db->prepare('SELECT id FROM items WHERE label_hash = ?');
        $q->execute([$row['label_hash']]);
        reply(409, ['id' => (int)$q->fetchColumn(), 'error' => 'already in inventory']);
    }
    if (strpos($msg, 'constraint') !== false) {   // unknown type, bad mount, negative qty, ...
        reply(422, ['error' => $msg]);
    }
    error_log("inventory api: $msg");
    reply(500, ['error' => 'database error']);
}
