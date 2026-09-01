-- Usage becomes its own permission.
--
-- The Usage panel on a call - prompt tokens, cached tokens, TTS characters,
-- audio seconds - was left open when cost was gated, and the two are the same
-- disclosure. Anyone who can see the token counts and knows the rates can work
-- the price out; hiding only the rupee figure was half a control.
--
-- Given to every role that can already see costs, which today is all four. The
-- point is not to take anything away here - it is to make it possible to, from
-- the roles page, deliberately.

INSERT INTO role_permissions (role_id, permission)
SELECT r.id, 'usage.read'
  FROM roles r
 WHERE r.builtin
    OR EXISTS (SELECT 1 FROM role_permissions rp
                WHERE rp.role_id = r.id AND rp.permission = 'cost.read')
ON CONFLICT DO NOTHING;

SELECT r.key, count(rp.permission) AS permissions,
       bool_or(rp.permission = 'usage.read') AS has_usage
  FROM roles r LEFT JOIN role_permissions rp ON rp.role_id = r.id
 GROUP BY r.id, r.key ORDER BY r.id;
