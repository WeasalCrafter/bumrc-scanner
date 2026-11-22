<?php
// Shared database connection + HTML escaping. Used by every page and the API.

// pulls the config which includes the API Key, password hash, and database file location
function config(): array {
    static $cfg = null;
    if ($cfg === null) {
        // INVENTORY_CONFIG is only set for local testing; on the server the default path is used.
        $cfg = require (getenv('INVENTORY_CONFIG') ?: '/home/logavdfu/inventory-data/config.php');
    }
    return $cfg;
}

// helper function for accessing the SQLite database
function db(): PDO {
    static $pdo = null;
    if ($pdo === null) {
        $pdo = new PDO('sqlite:' . config()['db_path']);
        $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
        $pdo->setAttribute(PDO::ATTR_DEFAULT_FETCH_MODE, PDO::FETCH_ASSOC);
        $pdo->exec('PRAGMA foreign_keys = ON');
        $pdo->exec('PRAGMA busy_timeout = 5000');   // wait instead of failing if another request is writing
    }
    return $pdo;
}

// helper function for showing all values as plain-text, disregards html tags <> in the values of the database
function h($s): string {
    return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8');
}
