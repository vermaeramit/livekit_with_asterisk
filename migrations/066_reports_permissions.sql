-- Grant the Reports permissions to the role that is defined as holding all of them.
--
-- A TRAP WORTH WRITING DOWN, because it will catch the next person too.
-- permissions.PERMISSIONS is the list the code enforces; role_permissions is
-- the list each role actually HAS, seeded once by migration 030 as explicit
-- rows. Adding a key to the dict grants it to nobody - not even superadmin,
-- whose SEED_ROLES entry reads `tuple(PERMISSIONS)` and therefore captured
-- whatever the dict held on the day that seed ran.
--
-- deps.py says as much and it is worth re-reading: "the superadmin role holds
-- every permission because migration 030 gives it every permission, which is a
-- fact in the database somebody could look at - not a branch in here that no
-- permission list can describe."
--
-- So the console hid the whole Reports section after reports.read and
-- reports.export were added, and nothing was wrong with the page.
--
-- ONLY SUPERADMIN HERE, deliberately. The seed's own rule is that a migration
-- reproduces the access people have today and changes nothing: nobody had
-- reports yesterday because reports did not exist. Superadmin is the exception
-- because its description is "Everything, across every client" - leaving it
-- short of a permission makes that description false.
--
-- Everyone else is granted from Roles, by somebody looking at the console.
-- That matters most for reports.export: reading a figure leaves it here, a
-- file does not.
--
-- Written as a join against the role key rather than a hardcoded id, and
-- ON CONFLICT DO NOTHING so it is safe to re-run.

INSERT INTO role_permissions (role_id, permission)
SELECT r.id, v.permission
  FROM (VALUES ('reports.read'), ('reports.export')) AS v(permission)
  CROSS JOIN roles r
 WHERE r.key = 'superadmin'
ON CONFLICT (role_id, permission) DO NOTHING;

-- What superadmin holds now, and anything the code enforces that NO role has.
-- The second list is the one to read: a permission nobody holds is a page
-- nobody can open, and that is exactly how this migration came to be needed.
SELECT r.key, count(*) AS permissions
  FROM roles r JOIN role_permissions rp ON rp.role_id = r.id
 GROUP BY r.key ORDER BY r.key;

SELECT v.permission AS held_by_no_role
  FROM (VALUES ('reports.read'), ('reports.export')) AS v(permission)
 WHERE NOT EXISTS (SELECT 1 FROM role_permissions rp
                    WHERE rp.permission = v.permission);
