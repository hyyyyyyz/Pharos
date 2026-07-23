"""Collections and tags: ownership, nesting, naming, and the computed counts.

The security cases are the point of this file. Pharos is multi-user and the
organise endpoints take *ids in a request body* — a folder id in the path plus
paper ids in the payload — so there are two independent chances to touch a row
that is not the caller's, and both must fail identically to a row that does not
exist. Every ``NotFound`` assertion below is really an assertion that a probe
cannot distinguish "not yours" from "no such thing" and therefore cannot walk
ids to enumerate another researcher's library.

The rest pins the behaviour that had to be *decided* rather than derived:
deleting a folder promotes its children, names are unique per sibling group
ignoring case, 未分类 is computed, and no count ever includes a trashed paper.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pharos.db.models import Collection, Paper, PaperCollection, Tag, User
from pharos.db.session import init_engine, session_scope
from pharos.services import organise
from pharos.services.organise import Conflict, Invalid, NotFound
from sqlalchemy import delete

#: Prefixed rather than named "owner"/"other" because ``init_engine`` memoises:
#: whichever test module runs first wins, and every later one shares that
#: database. Ids scoped to this module cannot collide with another module's
#: fixture users, and — more importantly — cannot be *deleted* by one.
OWNER = "organise-owner"
OTHER = "organise-other"
_USERS = (OWNER, OTHER)


@pytest.fixture(scope="module", autouse=True)
def _db(tmp_path_factory: pytest.TempPathFactory) -> None:
    """Ensure a database and this module's two users exist.

    The path is only honoured if this module is the first to call
    ``init_engine``; otherwise the call is a no-op returning the existing
    engine. So the user insert has to be written as "create if absent" rather
    than a plain add, which would hit a unique violation on a second run
    against a shared database.
    """
    init_engine(tmp_path_factory.mktemp("db") / "pharos.db")
    with session_scope() as s:
        for uid in _USERS:
            if s.get(User, uid) is None:
                s.add(User(id=uid, email=f"{uid}@example.test", password_hash="x"))


@pytest.fixture(autouse=True)
def _clean() -> None:
    """Wipe *this module's* organise rows between tests.

    The counts are per user and global across the module, so a leftover folder
    from an earlier test would make an assertion about "the user's tree" pass or
    fail depending on test order — the kind of flake that gets a real failure
    marked as noise.

    Scoped to ``_USERS`` rather than truncating the tables, because the engine
    is shared with every other test module and deleting their papers out from
    under them would make this file's cleanup another file's failure. The link
    tables are not listed: ``paper_collections`` and ``paper_tags`` cascade from
    both of their parents, so removing the papers, folders and tags takes them.
    """
    with session_scope() as s:
        s.execute(delete(Paper).where(Paper.user_id.in_(_USERS)))
        s.execute(delete(Collection).where(Collection.user_id.in_(_USERS)))
        s.execute(delete(Tag).where(Tag.user_id.in_(_USERS)))


def _paper(name: str, *, user_id: str = OWNER, trashed: bool = False) -> str:
    """Insert a paper and return its id, namespaced to this module.

    The namespace is not cosmetic: the engine is shared across test modules and
    ``papers.id`` is the primary key, so a bare "live" here would collide with
    the identically named row another module's fixture owns.
    """
    paper_id = f"organise-{name}"
    with session_scope() as s:
        s.add(
            Paper(
                id=paper_id,
                user_id=user_id,
                title=f"Paper {name}",
                orig_sha256=f"sha-{paper_id}",
                orig_filename=f"{name}.pdf",
                deleted_at=datetime.now(timezone.utc) if trashed else None,
            )
        )
    return paper_id


def _folder(name: str, *, user_id: str = OWNER, parent_id: str | None = None) -> str:
    with session_scope() as s:
        return organise.create_collection(
            s, user_id=user_id, name=name, parent_id=parent_id
        ).id


# ------------------------------------------------------------------ ownership


def test_cannot_file_another_users_paper() -> None:
    """The folder is mine, the paper is theirs. 404, and nothing is written."""
    folder = _folder("生成模型")
    theirs = _paper("theirs", user_id=OTHER)
    with session_scope() as s, pytest.raises(NotFound):
            organise.add_papers(s, user_id=OWNER, collection_id=folder, paper_ids=[theirs])
    with session_scope() as s:
        assert organise.collection_node(s, folder, user_id=OWNER).paper_count == 0


def test_cannot_file_into_another_users_folder() -> None:
    """The paper is mine, the folder is theirs. Same 404, opposite direction."""
    theirs = _folder("他们的", user_id=OTHER)
    mine = _paper("mine")
    with session_scope() as s, pytest.raises(NotFound):
            organise.add_papers(s, user_id=OWNER, collection_id=theirs, paper_ids=[mine])


def test_a_partial_batch_writes_nothing() -> None:
    """One foreign id in a list of mine fails the whole call.

    Filing the valid ids and silently dropping the rest would tell the caller,
    by way of the resulting count, exactly which id was not theirs.
    """
    folder = _folder("批量")
    mine = _paper("bulk-mine")
    theirs = _paper("bulk-theirs", user_id=OTHER)
    with session_scope() as s, pytest.raises(NotFound):
            organise.add_papers(
                s, user_id=OWNER, collection_id=folder, paper_ids=[mine, theirs]
            )
    with session_scope() as s:
        assert organise.collection_node(s, folder, user_id=OWNER).paper_count == 0


def test_another_users_folder_is_indistinguishable_from_a_missing_one() -> None:
    theirs = _folder("他们的", user_id=OTHER)
    with session_scope() as s:
        with pytest.raises(NotFound):
            organise.collection_node(s, theirs, user_id=OWNER)
        with pytest.raises(NotFound):
            organise.collection_node(s, "no-such-id", user_id=OWNER)
        with pytest.raises(NotFound):
            organise.delete_collection(s, user_id=OWNER, collection_id=theirs)


def test_cannot_nest_under_another_users_folder() -> None:
    theirs = _folder("他们的", user_id=OTHER)
    with session_scope() as s, pytest.raises(NotFound):
            organise.create_collection(s, user_id=OWNER, name="子", parent_id=theirs)


def test_cannot_apply_another_users_tag() -> None:
    paper = _paper("tagged")
    with session_scope() as s:
        theirs = organise.create_tag(s, user_id=OTHER, name="他们的").id
    with session_scope() as s, pytest.raises(NotFound):
            organise.set_paper_tags(s, user_id=OWNER, paper_id=paper, tag_ids=[theirs])
    with session_scope() as s:
        assert organise.paper_tags(s, user_id=OWNER, paper_id=paper) == []


def test_cannot_tag_another_users_paper() -> None:
    theirs = _paper("their-paper", user_id=OTHER)
    with session_scope() as s:
        mine = organise.create_tag(s, user_id=OWNER, name="我的").id
        with pytest.raises(NotFound):
            organise.set_paper_tags(s, user_id=OWNER, paper_id=theirs, tag_ids=[mine])


def test_one_users_tree_never_shows_anothers() -> None:
    _folder("我的")
    _folder("他们的", user_id=OTHER)
    with session_scope() as s:
        mine = organise.overview(s, user_id=OWNER)
        assert [c.name for c in mine.collections] == ["我的"]


def test_a_falsy_owner_id_is_a_programming_error_not_a_wide_query() -> None:
    """``None`` would render ``user_id IS NULL`` and match the legacy rows."""
    with session_scope() as s, pytest.raises(ValueError):
            organise.overview(s, user_id="")


# --------------------------------------------------------------------- counts


def test_counts_exclude_trashed_papers() -> None:
    folder = _folder("综述")
    live = _paper("live")
    dead = _paper("dead", trashed=True)
    with session_scope() as s:
        organise.add_papers(s, user_id=OWNER, collection_id=folder, paper_ids=[live, dead])
    with session_scope() as s:
        view = organise.overview(s, user_id=OWNER)
        assert view.collections[0].paper_count == 1
        assert view.all_count == 1


def test_a_trashed_paper_is_not_uncategorised_either() -> None:
    """It is in the recycle bin, which is a different view entirely."""
    _paper("kept")
    _paper("binned", trashed=True)
    with session_scope() as s:
        assert organise.count_uncategorised(s, user_id=OWNER) == 1


def test_uncategorised_is_computed_from_membership() -> None:
    folder = _folder("已归档")
    filed = _paper("filed")
    _paper("loose")  # filed nowhere — this is the one 未分类 must find
    with session_scope() as s:
        organise.add_papers(s, user_id=OWNER, collection_id=folder, paper_ids=[filed])
    with session_scope() as s:
        assert organise.count_uncategorised(s, user_id=OWNER) == 1
    # ...and deleting the folder must move `filed` back into it, with no
    # bookkeeping of our own: the membership row cascades away.
    with session_scope() as s:
        organise.delete_collection(s, user_id=OWNER, collection_id=folder)
    with session_scope() as s:
        assert organise.count_uncategorised(s, user_id=OWNER) == 2


def test_counts_are_direct_membership_not_a_rollup() -> None:
    parent = _folder("机器学习")
    child = _folder("扩散模型", parent_id=parent)
    with session_scope() as s:
        organise.add_papers(
            s, user_id=OWNER, collection_id=child, paper_ids=[_paper("p1")]
        )
    with session_scope() as s:
        tree = organise.overview(s, user_id=OWNER).collections
        assert tree[0].paper_count == 0
        assert tree[0].children[0].paper_count == 1


def test_another_users_membership_cannot_inflate_a_count() -> None:
    """A row naming my folder and their paper is not counted, however it got there."""
    folder = _folder("我的")
    theirs = _paper("their-paper", user_id=OTHER)
    with session_scope() as s:
        s.add(PaperCollection(paper_id=theirs, collection_id=folder))
    with session_scope() as s:
        assert organise.collection_node(s, folder, user_id=OWNER).paper_count == 0


# -------------------------------------------------------------------- nesting


def test_a_folder_cannot_become_its_own_parent() -> None:
    folder = _folder("自环")
    with session_scope() as s, pytest.raises(Invalid):
            organise.update_collection(
                s, user_id=OWNER, collection_id=folder, changes={"parent_id": folder}
            )


def test_a_folder_cannot_move_inside_its_own_descendant() -> None:
    """The subtle cycle: grandparent under grandchild orphans the whole branch."""
    root = _folder("根")
    mid = _folder("中", parent_id=root)
    leaf = _folder("叶", parent_id=mid)
    with session_scope() as s, pytest.raises(Invalid):
            organise.update_collection(
                s, user_id=OWNER, collection_id=root, changes={"parent_id": leaf}
            )
    with session_scope() as s:
        tree = organise.overview(s, user_id=OWNER).collections
        assert [c.name for c in tree] == ["根"]
        assert tree[0].children[0].children[0].id == leaf


def test_nesting_is_bounded() -> None:
    parent: str | None = None
    for i in range(organise._MAX_DEPTH):
        parent = _folder(f"level-{i}", parent_id=parent)
    with session_scope() as s, pytest.raises(Invalid):
            organise.create_collection(s, user_id=OWNER, name="too-deep", parent_id=parent)


def test_moving_a_tall_subtree_respects_the_depth_limit() -> None:
    """The moved node's own height counts, not just its new parent's depth."""
    deep: str | None = None
    for i in range(organise._MAX_DEPTH - 1):
        deep = _folder(f"chain-{i}", parent_id=deep)
    tall_root = _folder("tall")
    _folder("tall-child", parent_id=tall_root)
    with session_scope() as s, pytest.raises(Invalid):
            organise.update_collection(
                s, user_id=OWNER, collection_id=tall_root, changes={"parent_id": deep}
            )


def test_moving_to_the_top_level_is_distinguishable_from_not_moving() -> None:
    root = _folder("根")
    child = _folder("子", parent_id=root)
    with session_scope() as s:
        organise.update_collection(
            s, user_id=OWNER, collection_id=child, changes={"parent_id": None}
        )
    with session_scope() as s:
        names = sorted(c.name for c in organise.overview(s, user_id=OWNER).collections)
        assert names == ["子", "根"]


def test_renaming_leaves_the_parent_alone() -> None:
    """A patch that omits ``parent_id`` must not silently promote the folder."""
    root = _folder("根")
    child = _folder("子", parent_id=root)
    with session_scope() as s:
        organise.update_collection(
            s, user_id=OWNER, collection_id=child, changes={"name": "子二"}
        )
    with session_scope() as s:
        tree = organise.overview(s, user_id=OWNER).collections
        assert [c.name for c in tree] == ["根"]
        assert tree[0].children[0].name == "子二"


# --------------------------------------------------------------------- naming


def test_sibling_names_are_unique_ignoring_case() -> None:
    _folder("Diffusion")
    with session_scope() as s, pytest.raises(Conflict):
            organise.create_collection(s, user_id=OWNER, name="diffusion")


def test_the_same_name_under_a_different_parent_is_allowed() -> None:
    """Two "综述" folders in two different places are two distinguishable places."""
    a = _folder("A")
    b = _folder("B")
    _folder("综述", parent_id=a)
    with session_scope() as s:
        organise.create_collection(s, user_id=OWNER, name="综述", parent_id=b)


def test_two_users_may_hold_the_same_folder_name() -> None:
    _folder("生成模型")
    with session_scope() as s:
        organise.create_collection(s, user_id=OTHER, name="生成模型")


def test_whitespace_is_collapsed_before_the_uniqueness_check() -> None:
    _folder("deep RL")
    with session_scope() as s, pytest.raises(Conflict):
            organise.create_collection(s, user_id=OWNER, name="  deep   RL  ")


def test_a_blank_name_is_refused() -> None:
    with session_scope() as s, pytest.raises(Invalid):
            organise.create_collection(s, user_id=OWNER, name="   ")


def test_a_null_name_is_not_stringified_into_the_word_none() -> None:
    folder = _folder("真名")
    with session_scope() as s, pytest.raises(Invalid):
            organise.update_collection(
                s, user_id=OWNER, collection_id=folder, changes={"name": None}
            )


def test_tags_differing_only_by_case_are_the_same_tag() -> None:
    with session_scope() as s:
        organise.create_tag(s, user_id=OWNER, name="NLP")
    with session_scope() as s, pytest.raises(Conflict):
            organise.create_tag(s, user_id=OWNER, name="nlp")


def test_a_tag_can_be_recased() -> None:
    """Excluding the tag from its own check is what makes this legal."""
    with session_scope() as s:
        tag = organise.create_tag(s, user_id=OWNER, name="nlp").id
    with session_scope() as s:
        organise.update_tag(s, user_id=OWNER, tag_id=tag, changes={"name": "NLP"})
    with session_scope() as s:
        assert [t.tag.name for t in organise.list_tags(s, user_id=OWNER)] == ["NLP"]


def test_a_colour_must_be_a_token_not_a_hex() -> None:
    """A hex in the database is a colour no theme could ever override."""
    with session_scope() as s:
        with pytest.raises(Invalid):
            organise.create_tag(s, user_id=OWNER, name="红", color="#ff0000")
        organise.create_tag(s, user_id=OWNER, name="琥珀", color="amber")


# -------------------------------------------------------------------- deleting


def test_deleting_a_folder_promotes_its_children() -> None:
    root = _folder("根")
    mid = _folder("中", parent_id=root)
    leaf = _folder("叶", parent_id=mid)
    with session_scope() as s:
        assert organise.delete_collection(s, user_id=OWNER, collection_id=mid) == 1
    with session_scope() as s:
        tree = organise.overview(s, user_id=OWNER).collections
        assert [c.name for c in tree] == ["根"]
        assert [c.id for c in tree[0].children] == [leaf]


def test_deleting_a_folder_keeps_its_papers() -> None:
    folder = _folder("临时")
    paper = _paper("survivor")
    with session_scope() as s:
        organise.add_papers(s, user_id=OWNER, collection_id=folder, paper_ids=[paper])
    with session_scope() as s:
        organise.delete_collection(s, user_id=OWNER, collection_id=folder)
    with session_scope() as s:
        assert s.get(Paper, paper) is not None
        assert organise.count_uncategorised(s, user_id=OWNER) == 1


def test_deleting_a_tag_keeps_its_papers() -> None:
    paper = _paper("kept")
    with session_scope() as s:
        tag = organise.create_tag(s, user_id=OWNER, name="待读").id
        organise.set_paper_tags(s, user_id=OWNER, paper_id=paper, tag_ids=[tag])
    with session_scope() as s:
        organise.delete_tag(s, user_id=OWNER, tag_id=tag)
    with session_scope() as s:
        assert s.get(Paper, paper) is not None
        assert organise.paper_tags(s, user_id=OWNER, paper_id=paper) == []


def test_deleting_another_users_tag_is_a_404() -> None:
    with session_scope() as s:
        theirs = organise.create_tag(s, user_id=OTHER, name="他们的").id
    with session_scope() as s, pytest.raises(NotFound):
            organise.delete_tag(s, user_id=OWNER, tag_id=theirs)
    with session_scope() as s:
        assert s.get(Tag, theirs) is not None


# ---------------------------------------------------------------- membership


def test_filing_the_same_paper_twice_is_idempotent() -> None:
    """A double-click must not be an error, and must not double the count."""
    folder = _folder("重复")
    paper = _paper("once")
    with session_scope() as s:
        assert organise.add_papers(
            s, user_id=OWNER, collection_id=folder, paper_ids=[paper, paper]
        ) == 1
    with session_scope() as s:
        assert organise.add_papers(
            s, user_id=OWNER, collection_id=folder, paper_ids=[paper]
        ) == 0
        assert organise.collection_node(s, folder, user_id=OWNER).paper_count == 1


def test_a_paper_can_sit_in_several_folders() -> None:
    a = _folder("A")
    b = _folder("B")
    paper = _paper("shared")
    with session_scope() as s:
        organise.add_papers(s, user_id=OWNER, collection_id=a, paper_ids=[paper])
        organise.add_papers(s, user_id=OWNER, collection_id=b, paper_ids=[paper])
    with session_scope() as s:
        assert organise.count_uncategorised(s, user_id=OWNER) == 0
        assert organise.collection_node(s, a, user_id=OWNER).paper_count == 1
        assert organise.collection_node(s, b, user_id=OWNER).paper_count == 1


def test_removing_a_paper_that_is_not_filed_here_is_a_404() -> None:
    folder = _folder("空")
    paper = _paper("elsewhere")
    with session_scope() as s, pytest.raises(NotFound):
            organise.remove_paper(
                s, user_id=OWNER, collection_id=folder, paper_id=paper
            )


def test_removing_a_paper_leaves_the_paper_alone() -> None:
    folder = _folder("暂存")
    paper = _paper("moved-out")
    with session_scope() as s:
        organise.add_papers(s, user_id=OWNER, collection_id=folder, paper_ids=[paper])
    with session_scope() as s:
        organise.remove_paper(s, user_id=OWNER, collection_id=folder, paper_id=paper)
    with session_scope() as s:
        assert s.get(Paper, paper) is not None
        assert organise.collection_node(s, folder, user_id=OWNER).paper_count == 0
        assert organise.count_uncategorised(s, user_id=OWNER) == 1


# --------------------------------------------------------------------- tagging


def test_setting_tags_replaces_rather_than_merges() -> None:
    """The client sends the complete intended state; a merge makes unchecking
    impossible."""
    paper = _paper("relabelled")
    with session_scope() as s:
        a = organise.create_tag(s, user_id=OWNER, name="A").id
        b = organise.create_tag(s, user_id=OWNER, name="B").id
        organise.set_paper_tags(s, user_id=OWNER, paper_id=paper, tag_ids=[a, b])
    with session_scope() as s:
        result = organise.set_paper_tags(s, user_id=OWNER, paper_id=paper, tag_ids=[b])
        assert [t.id for t in result] == [b]


def test_an_empty_tag_list_clears_every_tag() -> None:
    paper = _paper("cleared")
    with session_scope() as s:
        tag = organise.create_tag(s, user_id=OWNER, name="A").id
        organise.set_paper_tags(s, user_id=OWNER, paper_id=paper, tag_ids=[tag])
    with session_scope() as s:
        assert organise.set_paper_tags(s, user_id=OWNER, paper_id=paper, tag_ids=[]) == []
    with session_scope() as s:
        assert s.get(Tag, tag) is not None  # the tag itself survives


def test_tag_counts_exclude_trashed_papers() -> None:
    live = _paper("live")
    dead = _paper("dead", trashed=True)
    with session_scope() as s:
        tag = organise.create_tag(s, user_id=OWNER, name="待读").id
        organise.set_paper_tags(s, user_id=OWNER, paper_id=live, tag_ids=[tag])
        organise.set_paper_tags(s, user_id=OWNER, paper_id=dead, tag_ids=[tag])
    with session_scope() as s:
        counts = {t.tag.name: t.paper_count for t in organise.list_tags(s, user_id=OWNER)}
        assert counts == {"待读": 1}


def test_tag_list_is_scoped_to_the_caller() -> None:
    with session_scope() as s:
        organise.create_tag(s, user_id=OWNER, name="我的")
        organise.create_tag(s, user_id=OTHER, name="他们的")
    with session_scope() as s:
        assert [t.tag.name for t in organise.list_tags(s, user_id=OWNER)] == ["我的"]
