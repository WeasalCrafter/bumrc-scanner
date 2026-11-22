<?php
$cfg = require '/home/logavdfu/inventory-data/config.php';
session_set_cookie_params(['secure' => true, 'httponly' => true, 'samesite' => 'Lax']);
session_start();

if (isset($_POST['pw'])) {
    if (password_verify($_POST['pw'], $cfg['pw_hash'])) {
        session_regenerate_id(true);
        $_SESSION['ok'] = true;
        header('Location: ' . $_SERVER['REQUEST_URI']);   // drop the form POST so refresh doesn't resubmit
        exit;
    }
    sleep(1);                                             // slows down password guessing
    $err = '<p style="color: #D32F2F">Wrong password</p>';
}
if (empty($_SESSION['ok'])) { ?>
<!doctype html><meta name="viewport" content="width=device-width">

<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Login</title>
    <link rel="stylesheet" href="styles/main.css">
</head>

<body style="margin: 30px auto; text-align: center;">
    <form method="post">
        <?= isset($err) ? "<p>$err</p>" : '' ?>
            Password: <input type="password" name="pw" autofocus>
        <button>Enter</button>
    </form>
</body>

<?php exit; }
