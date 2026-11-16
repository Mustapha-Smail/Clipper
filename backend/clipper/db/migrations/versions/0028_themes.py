"""themes: the home of ideas, scripts and videos; post statistics; links of videos published by hand

Every theme text already used by an idea or a script (and the Studio theme in
Settings) becomes a theme row, and the ideas and scripts are linked to it. A
script follows its idea's theme when it has one.

Revision ID: a3c9e1f7b204
Revises: f2b8d4e6a913
Create Date: 2026-11-16 20:00:00
"""
import sqlalchemy as sa
from alembic import op

revision = 'a3c9e1f7b204'
down_revision = 'f2b8d4e6a913'
branch_labels = None
depends_on = None

COLORS = ("amber", "coral", "violet", "teal", "sky", "lime", "rose", "indigo")


def upgrade() -> None:
    op.create_table(
        'themes',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('color', sa.String(16), nullable=False, server_default='amber'),
        sa.Column('status', sa.String(12), nullable=False, server_default='active'),
        sa.Column('channels', sa.JSON(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=False, server_default=''),
        sa.Column('insights', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_themes_status', 'themes', ['status'])
    with op.batch_alter_table('ideas') as batch_op:
        batch_op.add_column(sa.Column('theme_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('inspired_by', sa.JSON(), nullable=True))
        batch_op.create_index('ix_ideas_theme_id', ['theme_id'])
        batch_op.create_foreign_key('fk_ideas_theme_id', 'themes', ['theme_id'], ['id'], ondelete='SET NULL')
    with op.batch_alter_table('scripts') as batch_op:
        batch_op.add_column(sa.Column('theme_id', sa.Integer(), nullable=True))
        batch_op.create_index('ix_scripts_theme_id', ['theme_id'])
        batch_op.create_foreign_key('fk_scripts_theme_id', 'themes', ['theme_id'], ['id'], ondelete='SET NULL')
    with op.batch_alter_table('posts') as batch_op:
        batch_op.add_column(sa.Column('metrics', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('metrics_at', sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table('storyboards') as batch_op:
        batch_op.add_column(sa.Column('published_url', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('stats', sa.JSON(), nullable=True))

    _link_existing()


def _link_existing() -> None:
    bind = op.get_bind()
    themes = sa.table('themes', sa.column('id', sa.Integer), sa.column('name', sa.Text),
                      sa.column('color', sa.String), sa.column('channels', sa.JSON))
    ideas = sa.table('ideas', sa.column('id', sa.Integer), sa.column('theme', sa.Text),
                     sa.column('theme_id', sa.Integer))
    scripts = sa.table('scripts', sa.column('id', sa.Integer), sa.column('theme', sa.Text),
                       sa.column('idea_id', sa.Integer), sa.column('theme_id', sa.Integer))
    settings = sa.table('settings', sa.column('key', sa.String), sa.column('value', sa.JSON))

    names: list[str] = []
    for (name,) in bind.execute(sa.select(ideas.c.theme).order_by(ideas.c.id)):
        if name and name.strip() and name.strip() not in names:
            names.append(name.strip())
    for (name,) in bind.execute(sa.select(scripts.c.theme).order_by(scripts.c.id)):
        if name and name.strip() and name.strip() not in names:
            names.append(name.strip())
    row = bind.execute(sa.select(settings.c.value).where(settings.c.key == 'app')).first()
    current = ((row[0] or {}) if row else {}).get('studio_theme') if row else None
    if isinstance(current, str) and current.strip() and current.strip() not in names:
        names.append(current.strip())

    ids: dict[str, int] = {}
    for i, name in enumerate(names):
        bind.execute(sa.insert(themes).values(name=name, color=COLORS[i % len(COLORS)], channels=[]))
        ids[name] = bind.execute(sa.select(themes.c.id).where(themes.c.name == name)
                                 .order_by(themes.c.id.desc())).scalar()
    for name, theme_id in ids.items():
        bind.execute(sa.update(ideas).where(ideas.c.theme == name).values(theme_id=theme_id))
        bind.execute(sa.update(scripts).where(scripts.c.theme == name).values(theme_id=theme_id))
    # A script follows its idea.
    for script_id, idea_id in bind.execute(sa.select(scripts.c.id, scripts.c.idea_id)
                                           .where(scripts.c.idea_id.is_not(None))).all():
        theme_id = bind.execute(sa.select(ideas.c.theme_id).where(ideas.c.id == idea_id)).scalar()
        if theme_id:
            bind.execute(sa.update(scripts).where(scripts.c.id == script_id).values(theme_id=theme_id))


def downgrade() -> None:
    with op.batch_alter_table('storyboards') as batch_op:
        batch_op.drop_column('stats')
        batch_op.drop_column('published_url')
    with op.batch_alter_table('posts') as batch_op:
        batch_op.drop_column('metrics_at')
        batch_op.drop_column('metrics')
    with op.batch_alter_table('scripts') as batch_op:
        batch_op.drop_constraint('fk_scripts_theme_id', type_='foreignkey')
        batch_op.drop_index('ix_scripts_theme_id')
        batch_op.drop_column('theme_id')
    with op.batch_alter_table('ideas') as batch_op:
        batch_op.drop_constraint('fk_ideas_theme_id', type_='foreignkey')
        batch_op.drop_index('ix_ideas_theme_id')
        batch_op.drop_column('inspired_by')
        batch_op.drop_column('theme_id')
    op.drop_index('ix_themes_status', 'themes')
    op.drop_table('themes')
