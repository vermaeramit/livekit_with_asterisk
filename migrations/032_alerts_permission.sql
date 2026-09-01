-- Alerts becomes its own permission.
--
-- The page was in the sidebar with no condition on it and its read endpoints
-- took any signed-in user, so an alert naming a client's error rate was visible
-- to everyone who could log in.
--
-- Acknowledging goes with reading rather than with editing: noticing an alert
-- and dealing with it is a different job from deciding what raises one, which
-- stays part of editing a campaign.
--
-- Given to every role that can already read calls, which today is all four.
-- Nothing is taken away here; this makes it possible to.

INSERT INTO role_permissions (role_id, permission)
SELECT r.id, 'alerts.read'
  FROM roles r
 WHERE r.builtin
    OR EXISTS (SELECT 1 FROM role_permissions rp
                WHERE rp.role_id = r.id AND rp.permission = 'calls.read')
ON CONFLICT DO NOTHING;

SELECT r.key, count(rp.permission) AS permissions,
       bool_or(rp.permission = 'alerts.read') AS has_alerts
  FROM roles r LEFT JOIN role_permissions rp ON rp.role_id = r.id
 GROUP BY r.id, r.key ORDER BY r.id;
