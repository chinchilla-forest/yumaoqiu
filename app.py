import datetime
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, session, flash
from sqlalchemy import create_engine, Column, Integer, String, Date, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, scoped_session
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config['SECRET_KEY'] = 'change-me'
app.config['DATABASE_URL'] = 'sqlite:///app.db'

engine = create_engine(app.config['DATABASE_URL'], future=True)
SessionLocal = scoped_session(sessionmaker(bind=engine, autoflush=False, autocommit=False))
Base = declarative_base()


class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    phone = Column(String(20), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)

    participations = relationship('Participation', back_populates='user')

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Event(Base):
    __tablename__ = 'events'
    id = Column(Integer, primary_key=True)
    date = Column(Date, nullable=False)
    created_by = Column(Integer, ForeignKey('users.id'), nullable=False)

    participants = relationship('Participation', back_populates='event', cascade='all, delete')
    creator = relationship('User')


class Participation(Base):
    __tablename__ = 'participations'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    event_id = Column(Integer, ForeignKey('events.id'), nullable=False)

    user = relationship('User', back_populates='participations')
    event = relationship('Event', back_populates='participants')

    __table_args__ = (UniqueConstraint('user_id', 'event_id', name='unique_participation'),)


Base.metadata.create_all(engine)


def get_db():
    return SessionLocal()


def login_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            flash('请先登录。', 'warning')
            return redirect(url_for('login'))
        return view_func(*args, **kwargs)
    return wrapper


def current_user():
    db = get_db()
    user_id = session.get('user_id')
    if user_id is None:
        return None
    return db.get(User, user_id)


@app.teardown_appcontext
def remove_session(exception=None):
    SessionLocal.remove()


@app.route('/')
def home():
    db = get_db()
    year = datetime.date.today().year
    events = (
        db.query(Event)
        .filter(func.strftime('%Y', Event.date) == str(year))
        .order_by(Event.date)
        .all()
    )

    # aggregate counts for current year by default
    stats = (
        db.query(User.phone, func.count(Participation.id))
        .join(Participation, Participation.user_id == User.id)
        .join(Event, Event.id == Participation.event_id)
        .filter(func.strftime('%Y', Event.date) == str(year))
        .group_by(User.id)
        .all()
    )

    user = current_user()
    return render_template('home.html', events=events, stats=stats, user=user, year=year)


@app.route('/register', methods=['GET', 'POST'])
def register():
    db = get_db()
    if request.method == 'POST':
        phone = request.form['phone'].strip()
        password = request.form['password']
        if db.query(User).filter_by(phone=phone).first():
            flash('手机号已注册。', 'danger')
            return redirect(url_for('register'))
        if not phone or not password:
            flash('请输入手机号和密码。', 'warning')
            return redirect(url_for('register'))
        user = User(phone=phone)
        user.set_password(password)
        db.add(user)
        db.commit()
        flash('注册成功，请登录。', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    db = get_db()
    if request.method == 'POST':
        phone = request.form['phone'].strip()
        password = request.form['password']
        user = db.query(User).filter_by(phone=phone).first()
        if user and user.check_password(password):
            session['user_id'] = user.id
            flash('登录成功。', 'success')
            return redirect(url_for('home'))
        flash('手机号或密码错误。', 'danger')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    flash('已退出登录。', 'info')
    return redirect(url_for('home'))


@app.route('/events/create', methods=['POST'])
@login_required
def create_event():
    db = get_db()
    date_str = request.form.get('date')
    try:
        event_date = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        flash('日期格式不正确。', 'danger')
        return redirect(url_for('home'))

    event = Event(date=event_date, created_by=session['user_id'])
    db.add(event)
    db.commit()
    flash('接龙已创建。', 'success')
    return redirect(url_for('home'))


@app.route('/events/<int:event_id>/join', methods=['POST'])
@login_required
def join_event(event_id):
    db = get_db()
    event = db.get(Event, event_id)
    if not event:
        flash('接龙不存在。', 'danger')
        return redirect(url_for('home'))

    existing = (
        db.query(Participation)
        .filter_by(event_id=event_id, user_id=session['user_id'])
        .first()
    )
    if existing:
        flash('已参与该接龙。', 'info')
        return redirect(url_for('home'))

    participation = Participation(event_id=event_id, user_id=session['user_id'])
    db.add(participation)
    db.commit()
    flash('已加入接龙。', 'success')
    return redirect(url_for('home'))


@app.route('/stats')
@login_required
def stats():
    db = get_db()
    view = request.args.get('view', 'year')
    today = datetime.date.today()

    if view == 'month':
        start_date = today.replace(day=1)
        stats_title = f"{today.year}年{today.month}月参与次数"
        date_filter = func.strftime('%Y-%m', Event.date) == start_date.strftime('%Y-%m')
    else:
        start_date = today.replace(month=1, day=1)
        stats_title = f"{today.year}年参与次数"
        date_filter = func.strftime('%Y', Event.date) == str(today.year)

    stats_data = (
        db.query(User.phone, func.count(Participation.id).label('count'))
        .join(Participation, Participation.user_id == User.id)
        .join(Event, Event.id == Participation.event_id)
        .filter(date_filter)
        .group_by(User.id)
        .order_by(func.count(Participation.id).desc())
        .all()
    )

    return render_template(
        'stats.html',
        stats=stats_data,
        stats_title=stats_title,
        view=view,
    )


if __name__ == '__main__':
    app.run(debug=True)
