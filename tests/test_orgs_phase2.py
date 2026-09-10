"""Phase 2 org/workspace + RBAC tests — repo-level with SQLite :memory:.

Covers:
  1. create org auto-owner
  2. RBAC invite forbidden for viewer (and member)
  3. member cannot remove others / admin limits
  4. list orgs isolation
Plus extra schema/idempotency checks.
"""
from src.store.orgs import OrganizationRepository
from src.domain.orgs import OrgRole, can_invite, can_remove, role_rank


def test_create_org_auto_owner():
    """Create org should auto-add creator as owner."""
    repo = OrganizationRepository(db_path=":memory:")
    org = repo.create_org("Acme Corp", "alice")
    assert org.name == "Acme Corp"
    assert org.created_by == "alice"
    assert org.id != ""
    # creator is owner
    role = repo.get_member_role(org.id, "alice")
    assert role == OrgRole.owner
    assert repo.is_member(org.id, "alice") is True
    # org retrievable
    fetched = repo.get(org.id)
    assert fetched is not None
    assert fetched.name == "Acme Corp"
    # membership list has one entry
    members = repo.list_members(org.id)
    assert len(members) == 1
    assert members[0].user_id == "alice"
    assert members[0].role == OrgRole.owner
    # list_for_user returns the org
    orgs = repo.list_for_user("alice")
    assert len(orgs) == 1
    assert orgs[0].id == org.id


def test_rbac_invite_forbidden_for_viewer_and_member():
    """Viewer and member cannot invite; admin limited; owner can invite all."""
    # pure RBAC helpers
    assert can_invite(OrgRole.viewer, OrgRole.viewer) is False
    assert can_invite(OrgRole.viewer, OrgRole.member) is False
    assert can_invite(OrgRole.viewer, OrgRole.admin) is False
    assert can_invite(OrgRole.viewer, OrgRole.owner) is False

    assert can_invite(OrgRole.member, OrgRole.member) is False
    assert can_invite(OrgRole.member, OrgRole.viewer) is False
    assert can_invite(OrgRole.member, OrgRole.admin) is False

    # admin can invite member/viewer only
    assert can_invite(OrgRole.admin, OrgRole.member) is True
    assert can_invite(OrgRole.admin, OrgRole.viewer) is True
    assert can_invite(OrgRole.admin, OrgRole.admin) is False
    assert can_invite(OrgRole.admin, OrgRole.owner) is False

    # owner can invite any role
    assert can_invite(OrgRole.owner, OrgRole.viewer) is True
    assert can_invite(OrgRole.owner, OrgRole.member) is True
    assert can_invite(OrgRole.owner, OrgRole.admin) is True
    assert can_invite(OrgRole.owner, OrgRole.owner) is True

    # repo-level: viewer actually stored as viewer cannot bypass via direct add
    # (repo add_member itself does not enforce RBAC; router does — we verify helper blocks)
    repo = OrganizationRepository(db_path=":memory:")
    org = repo.create_org("RBAC Org", "owner1")
    repo.add_member(org.id, "viewer1", OrgRole.viewer)
    viewer_role = repo.get_member_role(org.id, "viewer1")
    assert viewer_role == OrgRole.viewer
    # if viewer tries to invite someone, helper says no
    assert can_invite(viewer_role, OrgRole.member) is False


def test_member_cannot_remove_others():
    """Member cannot delete/remove others; viewer cannot; admin limited; owner can remove admin; self always."""
    # self-removal always allowed regardless of role
    assert can_remove(OrgRole.viewer, OrgRole.viewer, is_self=True) is True
    assert can_remove(OrgRole.member, OrgRole.owner, is_self=True) is True
    assert can_remove(None, None, is_self=True) is True

    # non-self: viewer/member cannot remove anyone
    assert can_remove(OrgRole.viewer, OrgRole.viewer, is_self=False) is False
    assert can_remove(OrgRole.viewer, OrgRole.member, is_self=False) is False
    assert can_remove(OrgRole.member, OrgRole.viewer, is_self=False) is False
    assert can_remove(OrgRole.member, OrgRole.member, is_self=False) is False
    assert can_remove(OrgRole.member, OrgRole.admin, is_self=False) is False

    # admin can remove member/viewer, not admin/owner
    assert can_remove(OrgRole.admin, OrgRole.member, is_self=False) is True
    assert can_remove(OrgRole.admin, OrgRole.viewer, is_self=False) is True
    assert can_remove(OrgRole.admin, OrgRole.admin, is_self=False) is False
    assert can_remove(OrgRole.admin, OrgRole.owner, is_self=False) is False

    # owner can remove anyone (including admin)
    assert can_remove(OrgRole.owner, OrgRole.viewer, is_self=False) is True
    assert can_remove(OrgRole.owner, OrgRole.member, is_self=False) is True
    assert can_remove(OrgRole.owner, OrgRole.admin, is_self=False) is True
    assert can_remove(OrgRole.owner, OrgRole.owner, is_self=False) is True

    # role_rank ordering sanity
    assert role_rank(OrgRole.owner) > role_rank(OrgRole.admin) > role_rank(OrgRole.member) > role_rank(OrgRole.viewer)

    # repo-level integration: membership removal respects reality
    repo = OrganizationRepository(db_path=":memory:")
    org = repo.create_org("Remove Org", "owner1")
    repo.add_member(org.id, "admin1", OrgRole.admin)
    repo.add_member(org.id, "member1", OrgRole.member)
    repo.add_member(org.id, "viewer1", OrgRole.viewer)

    # member1 should NOT be able to remove viewer1 per RBAC
    actor = repo.get_member_role(org.id, "member1")
    target = repo.get_member_role(org.id, "viewer1")
    assert can_remove(actor, target) is False

    # admin can remove viewer but not owner
    actor_admin = repo.get_member_role(org.id, "admin1")
    assert can_remove(actor_admin, target) is True
    assert can_remove(actor_admin, OrgRole.owner) is False

    # self leave works even for viewer
    assert can_remove(OrgRole.viewer, OrgRole.viewer, is_self=True) is True
    # simulate viewer leaving
    removed = repo.remove_member(org.id, "viewer1")
    assert removed is True
    assert repo.is_member(org.id, "viewer1") is False
    # other members still there
    assert repo.is_member(org.id, "member1") is True


def test_list_orgs_isolation():
    """List orgs isolation: user sees only orgs they are member of."""
    repo = OrganizationRepository(db_path=":memory:")
    # alice creates org_a, bob creates org_b
    org_a = repo.create_org("Alice Org", "alice")
    org_b = repo.create_org("Bob Org", "bob")
    # isolation: alice sees only A, bob sees only B
    alice_orgs = repo.list_for_user("alice")
    bob_orgs = repo.list_for_user("bob")
    assert len(alice_orgs) == 1
    assert alice_orgs[0].id == org_a.id
    assert len(bob_orgs) == 1
    assert bob_orgs[0].id == org_b.id
    # unknown user sees nothing
    assert repo.list_for_user("charlie") == []
    # alice not member of bob's org
    assert repo.is_member(org_b.id, "alice") is False
    assert repo.get_member_role(org_b.id, "alice") is None
    # invite bob to alice's org as member
    repo.add_member(org_a.id, "bob", OrgRole.member)
    # now bob sees both orgs
    bob_orgs2 = repo.list_for_user("bob")
    assert len(bob_orgs2) == 2
    ids = {o.id for o in bob_orgs2}
    assert org_a.id in ids and org_b.id in ids
    # alice still sees only her org
    assert len(repo.list_for_user("alice")) == 1
    # member list reflects both members
    members = repo.list_members(org_a.id)
    user_ids = {m.user_id for m in members}
    assert "alice" in user_ids and "bob" in user_ids


def test_schema_idempotent_and_indexes():
    """_ensure_schema should be idempotent and handle :memory: reuse."""
    repo = OrganizationRepository(db_path=":memory:")
    # creating multiple orgs reuses same :memory: connection without error
    org1 = repo.create_org("Org1", "u1")
    org2 = repo.create_org("Org2", "u1")
    assert org1.id != org2.id
    # new repo with file-like fallback also works — tables via CREATE IF NOT EXISTS
    repo2 = OrganizationRepository(db_path=":memory:")
    # separate instance has its own memory, still works
    org3 = repo2.create_org("Other", "u2")
    assert org3 is not None


def test_get_nonexistent_returns_none():
    repo = OrganizationRepository(db_path=":memory:")
    assert repo.get("nonexistent") is None
    assert repo.get_member_role("nonexistent", "alice") is None
    assert repo.is_member("nonexistent", "alice") is False
    assert repo.list_members("nonexistent") == []
