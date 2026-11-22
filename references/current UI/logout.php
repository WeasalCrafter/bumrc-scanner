<?php
session_set_cookie_params(['secure' => true, 'httponly' => true, 'samesite' => 'Lax']);
session_start();
$_SESSION = [];
session_destroy();
setcookie(session_name(), '', ['expires' => 1, 'path' => '/', 'secure' => true, 'httponly' => true, 'samesite' => 'Lax']);
header('Location: ./');
exit;
