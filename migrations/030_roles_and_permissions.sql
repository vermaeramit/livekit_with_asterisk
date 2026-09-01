-- Roles as data, and permissions per role.
--
-- Today the four roles promise four levels of access and deliver three:
-- `agent` and `viewer` are byte-for-byte identical, and reading is not guarded
-- at all - anyone who can log in sees every transcript, every recording and,
-- since last week, what every call cost.
--
-- Roles belong to the platform rather than to each client. A client assigns its
-- people to them and cannot edit the roles themselves, which is the difference
-- between a support request and a client locking itself out of its own console.
--
-- THE SEED REPRODUCES TODAY'S ACCESS EXACTLY. Nothing changes on the day this
-- runs. Tightening - taking `cost.read` off agents, say - is a decision made
-- afterwards with the console open, not a surprise found through a support call.
--
-- The canonical permission list lives in admin/backend/app/permissions.py. The
-- values below are copied from it; that file is the one to edit.

CREATE TABLE IF NOT EXISTS roles (
    id          BIGSERIAL PRIMARY KEY,
    key         TEXT        NOT NULL UNIQUE,
    name        TEXT        NOT NULL,
    description TEXT,
    -- Sees every client. NOT a permission: "which tenants" is a different
    -- question from "may do what", and folding it into the permission list
    -- would let someone hand themselves the whole platform by ticking a box.
    all_tenants BOOLEAN     NOT NULL DEFAULT false,
    -- Cannot be edited or deleted, and its permissions are always the full set.
    -- Exactly one role has this. Without it, one wrong save closes the console
    -- with no way back in.
    builtin     BOOLEAN     NOT NULL DEFAULT false,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS role_permissions (
    role_id    BIGINT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    permission TEXT   NOT NULL,
    PRIMARY KEY (role_id, permission)
);

-- RESTRICT, not CASCADE: deleting a role that people are using must fail
-- loudly rather than quietly leave them with no role at all.
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS role_id BIGINT REFERENCES roles(id) ON DELETE RESTRICT;

INSERT INTO roles (key, name, description, all_tenants, builtin) VALUES
    ('superadmin', 'Super Admin', 'Everything, across every client. Cannot be edited.', true, true),
    ('tenant_admin', 'Admin', 'Runs one client: campaigns, keys and users.', false, false),
    ('agent', 'Agent', 'Reads calls and dashboards. Changes nothing.', false, false),
    ('viewer', 'Viewer', 'Reads calls and dashboards. Changes nothing.', false, false)
ON CONFLICT (key) DO NOTHING;

INSERT INTO role_permissions (role_id, permission)
SELECT r.id, v.permission
  FROM (VALUES
    ('superadmin', 'calls.read'),
    ('superadmin', 'calls.recording'),
    ('superadmin', 'analytics.read'),
    ('superadmin', 'cost.read'),
    ('superadmin', 'live.read'),
    ('superadmin', 'gaps.read'),
    ('superadmin', 'campaign.write'),
    ('superadmin', 'provider_keys.write'),
    ('superadmin', 'users.manage'),
    ('superadmin', 'tenants.manage'),
    ('superadmin', 'rates.manage'),
    ('superadmin', 'system.manage'),
    ('tenant_admin', 'calls.read'),
    ('tenant_admin', 'calls.recording'),
    ('tenant_admin', 'analytics.read'),
    ('tenant_admin', 'cost.read'),
    ('tenant_admin', 'live.read'),
    ('tenant_admin', 'gaps.read'),
    ('tenant_admin', 'campaign.write'),
    ('tenant_admin', 'provider_keys.write'),
    ('tenant_admin', 'users.manage'),
    ('agent', 'calls.read'),
    ('agent', 'calls.recording'),
    ('agent', 'analytics.read'),
    ('agent', 'cost.read'),
    ('agent', 'live.read'),
    ('agent', 'gaps.read'),
    ('viewer', 'calls.read'),
    ('viewer', 'calls.recording'),
    ('viewer', 'analytics.read'),
    ('viewer', 'cost.read'),
    ('viewer', 'live.read'),
    ('viewer', 'gaps.read')
       ) AS v(role_key, permission)
  JOIN roles r ON r.key = v.role_key
ON CONFLICT DO NOTHING;

-- Everyone keeps exactly the role they have.
UPDATE users u SET role_id = r.id FROM roles r
 WHERE r.key = u.role AND u.role_id IS NULL;

-- users.role is left in place but is no longer read for authorisation. It stays
-- until a later migration can drop it without guessing whether anything else
-- still selects it.
COMMENT ON COLUMN users.role IS
    'Legacy. Authorisation reads users.role_id -> roles/role_permissions.';


-- users.role stays the thing code writes; users.role_id is derived from it.
--
-- One direction, decided here rather than in each router. Every path that
-- creates or edits a user already sets `role`, and a rule kept in the database
-- applies to the ones written next year too - which is not true of a line added
-- to two call sites today.
--
-- An unknown role name raises instead of leaving role_id NULL. A NULL there is
-- a user with no permissions at all: signed in, and refused everything, with
-- nothing to say why.
CREATE OR REPLACE FUNCTION users_sync_role_id() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    found_id BIGINT;
BEGIN
    SELECT id INTO found_id FROM roles WHERE key = NEW.role;
    IF found_id IS NULL THEN
        RAISE EXCEPTION 'no such role: %', NEW.role
              USING HINT = 'roles.key must already exist';
    END IF;
    NEW.role_id := found_id;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS users_role_id_sync ON users;
CREATE TRIGGER users_role_id_sync
    BEFORE INSERT OR UPDATE OF role ON users
    FOR EACH ROW EXECUTE FUNCTION users_sync_role_id();

SELECT r.key, r.name, r.all_tenants, r.builtin,
       count(rp.permission) AS permissions,
       (SELECT count(*) FROM users u WHERE u.role_id = r.id) AS users
  FROM roles r LEFT JOIN role_permissions rp ON rp.role_id = r.id
 GROUP BY r.id ORDER BY r.id;
