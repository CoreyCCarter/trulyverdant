from app import create_app
from app.extensions import db
from app.models import User, Article, Category, Tag, Invite

app = create_app()


@app.shell_context_processor
def make_shell_context():
    return {'db': db, 'User': User, 'Article': Article,
            'Category': Category, 'Tag': Tag, 'Invite': Invite}
