-- Raw-data reports. Output comments below were captured by executing each query in SQLite.
-- Money is formatted to two decimals; ORDER BY uses numeric sums, not formatted text.
-- Use 100.0 to prevent SQLite integer division.

-- a) Order totals. Revenue includes returned orders; this is not realized revenue.
-- total_orders | total_revenue | avg_order_value
-- 180 | 99860.20 | 554.78
SELECT COUNT(*) AS total_orders,
       printf('%.2f', SUM(o.quantity * p.price * (1 - COALESCE(o.discount_pct, 0) / 100.0))) AS total_revenue,
       printf('%.2f', AVG(o.quantity * p.price * (1 - COALESCE(o.discount_pct, 0) / 100.0))) AS avg_order_value
FROM orders o JOIN products p ON o.product_id = p.product_id;

-- b) COUNT(*) versus COUNT(rating): SQL NULL ratings are excluded only by COUNT(rating).
-- total_orders | rated_orders | missing_ratings
-- 180 | 165 | 15
SELECT COUNT(*) AS total_orders, COUNT(rating) AS rated_orders,
       COUNT(*) - COUNT(rating) AS missing_ratings
FROM orders;

-- c1) Genuine zero-match customer, using LEFT JOIN.
-- customer_id | name
-- C045 | Vihaan
SELECT c.customer_id, c.name
FROM customers c LEFT JOIN orders o ON c.customer_id = o.customer_id
GROUP BY c.customer_id, c.name
HAVING COUNT(o.order_id) = 0
ORDER BY c.customer_id;

-- c2) Independent confirmation. NOT IN is safe because orders.customer_id is NOT NULL.
-- customer_id | name
-- C045 | Vihaan
SELECT customer_id, name
FROM customers
WHERE customer_id NOT IN (SELECT DISTINCT customer_id FROM orders)
ORDER BY customer_id;

-- d) Cities with return rate above 20 percent.
-- city | total_orders | returned_orders | return_rate_pct
-- Jaipur | 19 | 8 | 42.1
-- Lucknow | 49 | 15 | 30.6
-- Bangalore | 33 | 8 | 24.2
SELECT c.city, COUNT(*) AS total_orders, SUM(o.returned) AS returned_orders,
       ROUND(100.0 * AVG(o.returned), 1) AS return_rate_pct
FROM orders o JOIN customers c ON o.customer_id = c.customer_id
GROUP BY c.city
HAVING return_rate_pct > 20
ORDER BY return_rate_pct DESC, c.city ASC;

-- e1) Top spenders. customer_id ASC makes pagination deterministic when spend ties.
-- customer_id | name | total_spend
-- C043 | Reyansh | 12920.00
-- C026 | Isha | 8371.60
-- C008 | Meera | 4564.60
-- C011 | Arjun | 4111.00
-- C042 | Sanya | 3785.00
SELECT c.customer_id, c.name, printf('%.2f', SUM(o.quantity * p.price * (1 - COALESCE(o.discount_pct, 0) / 100.0))) AS total_spend
FROM orders o
JOIN products p ON o.product_id = p.product_id
JOIN customers c ON o.customer_id = c.customer_id
GROUP BY c.customer_id, c.name
ORDER BY SUM(o.quantity * p.price * (1 - COALESCE(o.discount_pct, 0) / 100.0)) DESC, c.customer_id ASC
LIMIT 5;

-- e2) Same ranking with LIMIT/OFFSET returns positions three through five.
-- customer_id | name | total_spend
-- C008 | Meera | 4564.60
-- C011 | Arjun | 4111.00
-- C042 | Sanya | 3785.00
SELECT c.customer_id, c.name, printf('%.2f', SUM(o.quantity * p.price * (1 - COALESCE(o.discount_pct, 0) / 100.0))) AS total_spend
FROM orders o
JOIN products p ON o.product_id = p.product_id
JOIN customers c ON o.customer_id = c.customer_id
GROUP BY c.customer_id, c.name
ORDER BY SUM(o.quantity * p.price * (1 - COALESCE(o.discount_pct, 0) / 100.0)) DESC, c.customer_id ASC
LIMIT 3 OFFSET 2;

-- f) Category totals with a three-table join.
-- category | order_count | category_revenue
-- Haircare | 54 | 44956.10
-- Skincare | 60 | 27346.00
-- Babycare | 30 | 16805.00
-- PersonalCare | 36 | 10753.10
SELECT p.category, COUNT(*) AS order_count,
       printf('%.2f', SUM(o.quantity * p.price * (1 - COALESCE(o.discount_pct, 0) / 100.0))) AS category_revenue
FROM orders o
JOIN products p ON o.product_id = p.product_id
JOIN customers c ON o.customer_id = c.customer_id
GROUP BY p.category
ORDER BY SUM(o.quantity * p.price * (1 - COALESCE(o.discount_pct, 0) / 100.0)) DESC, p.category ASC;

-- g) Names starting with A.
-- customer_id | name
-- C001 | Aarav
-- C003 | Aditi
-- C004 | Ananya
-- C011 | Arjun
-- C021 | Aryan
-- C030 | Anika
-- C031 | Aditya
-- C036 | Aisha
-- C041 | Ayaan
-- C044 | Aria
SELECT customer_id, name FROM customers
WHERE name LIKE 'A%'
ORDER BY customer_id;

-- h) Distinct acquisition sources.
-- acquisition_source
-- Ad
-- Organic
-- Referral
-- Social
SELECT DISTINCT acquisition_source FROM customers ORDER BY acquisition_source;

-- i1) Add the loyalty column. Re-run schema + seed first before re-running reports.
-- (no result rows)
ALTER TABLE customers ADD COLUMN loyalty_tier VARCHAR(10);

-- i2) Assign every customer a loyalty tier in one CASE update.
-- (no result rows)
UPDATE customers
SET loyalty_tier = CASE WHEN city_tier = 1 THEN 'Gold' ELSE 'Silver' END;

-- i3) Verify loyalty assignment.
-- loyalty_tier | customer_count
-- Gold | 28
-- Silver | 17
SELECT loyalty_tier, COUNT(*) AS customer_count
FROM customers GROUP BY loyalty_tier ORDER BY loyalty_tier;
