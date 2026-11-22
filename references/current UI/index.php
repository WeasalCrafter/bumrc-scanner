<?php
var_dump(in_array('sqlite', PDO::getAvailableDrivers(), true));

require __DIR__ . '/auth.php';
require __DIR__ . '/db.php';
date_default_timezone_set('America/New_York'); // required for time display

// <?= means "print this"

// Sidebar categories. To add one, add a line: key => [label, mount, type]
// mount: 'SMD' / 'THT' / null (any)    type: 'RESISTOR', 'CAPACITOR', ... / null (any)
$CATEGORIES = [
    'all'         => ['All parts',           null,   null],
    'smd-res'     => ['Resistors',           null,   'RESISTOR'],
    'smd-cap'     => ['Capacitors',          null,   'CAPACITOR'],
    'smd-ind'     => ['Inductors',           null,   'INDUCTOR'],
    'smd-diode'   => ['Diodes',              null,   'DIODE'],
    'crystal'     => ['Crystal Oscillators', null,   'CRYSTAL'],
    'sensor'      => ['Sensors',             null,   'SENSOR'],
    'ic'          => ['ICs',                 null,   'IC'],  
];

$cat = $_GET['cat'] ?? 'all';
if (!isset($CATEGORIES[$cat])) $cat = 'all';
[$label, $mount, $type] = $CATEGORIES[$cat];
$search = trim($_GET['q'] ?? '');

// Build the WHERE clause from the category and the search box.
$where = [];
$params = [];
if ($mount) { $where[] = 'mount = ?'; $params[] = $mount; }
if ($type)  { $where[] = 'type = ?';  $params[] = $type; }
if ($search !== '') {
    $where[] = '(name LIKE ? OR mpn LIKE ? OR distributor_pn LIKE ? OR description LIKE ?)';
    array_push($params, ...array_fill(0, 4, "%$search%"));
}

$sql = 'SELECT name, description, mpn, distributor, distributor_pn, digikey_url,
               package, location, qty_remaining, original_qty, manufacturer, tolerance, updated_at
        FROM items'
     . ($where ? ' WHERE ' . implode(' AND ', $where) : '')
     . ' ORDER BY type, value_num, package, name';
$sql_query = db()->prepare($sql);
$sql_query->execute($params);
$items = $sql_query->fetchAll();
?>
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Inventory</title>
    <link rel="stylesheet" href="styles/main.css">
</head>
<body>

<header>
    <h1>BUMRC Electrical Parts Inventory</h1>
    <a class="button" href="add.php">+ Add part</a> <!-- Add part button, takes to add.php -->
    <a class="button" href="logout.php">Log out</a> <!-- Logout button, deletes the session login -->
</header>

<div class="layout">
    <nav>
        <?php foreach ($CATEGORIES as $key => $c): ?> <!-- Display all categories on the left, and if its selected make a border around it -->
            <a href="?cat=<?= h($key) ?>"<?= $key === $cat ? ' class="active"' : '' ?>><?= h($c[0]) ?></a>
        <?php endforeach ?>
    </nav>

    <main>
        <h2><?= h($label) ?> (<?= count($items)?>)</h2> <!-- The main label on the top of the screen saying "All Parts" or "SMD Resistors" -->

        <form method="get">
            <input type="hidden" name="cat" value="<?= h($cat) ?>"> <!-- Keeps the selected category for when you search -->
            <input class="search" type="search" name="q" value="<?= h($search) ?>" placeholder="Search parts..."> <!-- Search input for query form -->
        </form>

        <?php if (!$items): ?> <!-- If theres no items, then display a message explaining so -->
            <p>No parts found.</p>
        <?php else: ?> <!-- Otherwise, display each component -->
        <table>
            <tr><th>Part</th><th>Manufacturer</th><th>Package</th><th class="qty">Quantity (Date Updated)</th></tr> <!-- Define table columns -->
            <?php foreach ($items as $item): // iterate through all components and use $item as the current component
                $name = $item['name'] ?: ($item['description'] ?: ($item['mpn'] ?: '—')); // get name, otherwise, description, otherwise MPN, otherwise '-'
                $empty = (int)$item['qty_remaining'] === 0; // bool for whether the item is in stock or not
            ?>
            <tr<?= $empty ? ' class="empty"' : '' ?>> <!-- if $empty is true, change class to empty, changes CSS -->

                <!-- Display the $name as a link to the digikey webpage for the component, if available -->
                <td> 
                    <?php if ($item['digikey_url']): ?> 
                        <a href="<?= h($item['digikey_url']) ?>" target="_blank" rel="noopener"><?= h($name) ?></a>
                    <?php else: ?>
                        <?= h($name) ?>
                    <?php endif ?>
                    <div class="sub">
                        <?= h($item['distributor_pn'] ?? '') ?>
                        <?= $item['distributor'] && $item['distributor'] !== 'DIGIKEY' ? '(' . h($item['distributor']) . ')' : '' ?>
                    </div>
                </td>

                <!-- Display the manufacturer, if available -->
                <td><?= h($item['manufacturer'] ?? '-') ?></td> 

                <!-- Display the package size, ex (0603, 0805) -->
                <td><?= h($item['package'] ?? '-') ?></td>

                <!-- Display the quantity remaining, and how much was originally there, along with the date this was updated -->
                <td class="qty">
                    <?= h($item['qty_remaining']) ?> / <?= h($item['original_qty'] ?? '?') ?>
                    <span class="sub"> <?= h(date('M j, g:i A', strtotime($item['updated_at'] . ' UTC'))) ?> </span>
                </td> 
            </tr>
            <?php endforeach ?>
        </table>
        <?php endif ?>
    </main>
</div>

</body>
</html>
