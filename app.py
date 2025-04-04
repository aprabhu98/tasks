import os
from flask import (Flask, render_template, request, redirect, url_for, flash,
                   abort)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_, func
from datetime import date, datetime

# Auth and Form Imports
from flask_login import (LoginManager, UserMixin, login_user, logout_user,
                       login_required, current_user)
from flask_wtf import FlaskForm
# REMOVED Email validator, added StringField, PasswordField etc directly
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired, Length, EqualTo, ValidationError
from flask_bcrypt import Bcrypt
from flask_wtf.csrf import CSRFProtect

# --- Configuration ---
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_NAME = 'tasks.db'
instance_path = os.path.join(BASE_DIR, 'instance')
DB_URI = f'sqlite:///{os.path.join(instance_path, DB_NAME)}'

app = Flask(__name__)
app.config['SECRET_KEY'] = 'a_much_stronger_and_random_secret_key_please_change_v2' # CHANGE THIS!
app.config['SQLALCHEMY_DATABASE_URI'] = DB_URI
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

os.makedirs(instance_path, exist_ok=True)
db = SQLAlchemy(app)

# Initialize Extensions
bcrypt = Bcrypt(app)
csrf = CSRFProtect(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'

# Flask-Login User Loader
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- Database Models ---

# UPDATED: User Model (No Email)
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(20), unique=True, nullable=False)
    # email = db.Column(db.String(120), unique=True, nullable=False) # REMOVED Email
    password_hash = db.Column(db.String(60), nullable=False)
    tasks = db.relationship('Task', backref='owner', lazy=True) # Added backref for Task.owner

    def __repr__(self):
        return f"User('{self.username}')" # Updated repr

    def set_password(self, password):
        self.password_hash = bcrypt.generate_password_hash(password).decode('utf-8')

    def check_password(self, password):
        return bcrypt.check_password_hash(self.password_hash, password)

# Association table
task_tags = db.Table('task_tags',
    db.Column('task_id', db.Integer, db.ForeignKey('task.id'), primary_key=True),
    db.Column('tag_id', db.Integer, db.ForeignKey('tag.id'), primary_key=True)
)

# Task Model (Added explicit backref='owner' for clarity)
class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_complete = db.Column(db.Boolean, default=False)
    tags = db.relationship('Tag', secondary=task_tags, lazy='subquery',
                           backref=db.backref('tasks', lazy=True))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    # owner defined via backref in User model now

    def __repr__(self):
        return f'<Task {self.id}: {self.title}>'

class Tag(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)

    def __repr__(self):
        return f'<Tag {self.id}: {self.name}>'

# --- WTForms ---

# UPDATED: Registration Form (No Email)
class RegistrationForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=2, max=20)])
    # email = StringField('Email', validators=[DataRequired(), Email()]) # REMOVED Email Field
    password = PasswordField('Password', validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField('Sign Up')

    def validate_username(self, username):
        user = User.query.filter_by(username=username.data).first()
        if user:
            raise ValidationError('That username is already taken. Please choose a different one.')

    # def validate_email(self, email): # REMOVED Email Validator
    #     user = User.query.filter_by(email=email.data).first()
    #     if user:
    #         raise ValidationError('That email is already taken. Please choose a different one.')

# UPDATED: Login Form (Uses Username)
class LoginForm(FlaskForm):
    # email = StringField('Email', validators=[DataRequired(), Email()]) # REMOVED Email Field
    username = StringField('Username', validators=[DataRequired()]) # ADDED Username Field
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Login')


# --- Helper Functions ---
# ... (get_or_create_tag and process_tags remain the same) ...
def get_or_create_tag(tag_name):
    tag_name = tag_name.strip().lower()
    if not tag_name: return None
    tag = Tag.query.filter_by(name=tag_name).first()
    if not tag:
        tag = Tag(name=tag_name)
        db.session.add(tag)
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            flash(f"Error creating tag '{tag_name}': {e}", "error")
            tag = Tag.query.filter_by(name=tag_name).first()
            if not tag: return None
    return tag

def process_tags(tag_string):
     if not tag_string: return []
     tag_names = [name.strip() for name in tag_string.split(',') if name.strip()]
     tags = []
     for name in tag_names:
         tag_object = get_or_create_tag(name)
         if tag_object: tags.append(tag_object)
     return tags

# --- Context Processors ---
@app.context_processor
def inject_now():
    return {'now': datetime.utcnow()}

# --- Routes ---

# Index Route (No changes needed here, filtering already user-specific)
@app.route('/', methods=['GET'])
@login_required
def index():
    filter_tag = request.args.get('tag')
    filter_status = request.args.get('status')
    search_term = request.args.get('search')
    filter_date_str = request.args.get('created_on')

    query = Task.query.filter_by(user_id=current_user.id) # Base query is user-specific
    selected_date_obj = None

    # Apply search filter
    if search_term:
        like_pattern = f'%{search_term}%'
        query = query.filter(
            or_( Task.title.ilike(like_pattern), Task.description.ilike(like_pattern), Task.tags.any(Tag.name.ilike(like_pattern)) )
        )
    # Apply tag filter
    if filter_tag:
        tag = Tag.query.filter_by(name=filter_tag).first()
        if tag: query = query.filter(Task.tags.contains(tag))
    # Apply status filter
    if filter_status == 'complete': query = query.filter_by(is_complete=True)
    elif filter_status == 'incomplete': query = query.filter_by(is_complete=False)
    # Apply Date Filter
    if filter_date_str:
        try:
            selected_date_obj = date.fromisoformat(filter_date_str)
            query = query.filter(func.date(Task.created_at) == selected_date_obj)
        except ValueError:
            flash(f"Invalid date format for 'Created On'. Please use YYYY-MM-DD.", "error")
            filter_date_str = None

    tasks = query.order_by(Task.created_at.desc()).all()
    all_tags = Tag.query.order_by(Tag.name).all()

    return render_template('index.html',
                           tasks=tasks, all_tags=all_tags,
                           current_filter_tag=filter_tag, current_filter_status=filter_status,
                           current_search_term=search_term, current_filter_date=filter_date_str)

# Add Task Route (No changes needed, already uses current_user.id)
@app.route('/add', methods=['POST'])
@login_required
def add_task():
    title = request.form.get('title')
    description = request.form.get('description')
    tags_string = request.form.get('tags')
    if not title:
        flash('Task title is required!', 'error')
        return redirect(url_for('index'))
    new_task = Task(title=title, description=description, user_id=current_user.id)
    db.session.add(new_task)
    tags = process_tags(tags_string)
    new_task.tags.extend(tags)
    try:
        db.session.commit()
        flash('Task added successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error adding task: {e}', 'error')
    return redirect(url_for('index'))

# Update Task Route (No changes needed, already filters by user_id)
@app.route('/update/<int:task_id>', methods=['GET', 'POST'])
@login_required
def update_task(task_id):
    task = Task.query.filter_by(id=task_id, user_id=current_user.id).first_or_404()
    if request.method == 'POST':
        new_title = request.form.get('title')
        if not new_title: # Simplified validation check
            flash('Task title cannot be empty!', 'error')
            tags_str = ", ".join(tag.name for tag in task.tags)
            # Need csrf_token() if form uses FlaskForm, but not needed for manual rendering here
            return render_template('update_task.html', task=task, tags_string=tags_str) # Removed form=form

        task.title = new_title
        task.description = request.form.get('description')
        task.tags.clear()
        new_tags = process_tags(request.form.get('tags'))
        task.tags.extend(new_tags)
        try:
            db.session.commit()
            flash('Task updated successfully!', 'success')
            return redirect(url_for('index'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating task: {e}', 'error')
            tags_str = request.form.get('tags') # Keep user input on error
            return render_template('update_task.html', task=task, tags_string=tags_str) # Removed form=form

    tags_str = ", ".join(tag.name for tag in task.tags)
    return render_template('update_task.html', task=task, tags_string=tags_str) # Removed form=form


# Toggle Complete Route (No changes needed, already filters by user_id)
@app.route('/toggle/<int:task_id>', methods=['POST'])
@login_required
def toggle_complete(task_id):
    task = Task.query.filter_by(id=task_id, user_id=current_user.id).first_or_404()
    task.is_complete = not task.is_complete
    try:
        db.session.commit()
        status = "completed" if task.is_complete else "incomplete"
        flash(f'Task marked as {status}.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating task status: {e}', 'error')
    return redirect(request.referrer or url_for('index'))

# Delete Task Route (No changes needed, already filters by user_id)
@app.route('/delete/<int:task_id>', methods=['POST'])
@login_required
def delete_task(task_id):
    task = Task.query.filter_by(id=task_id, user_id=current_user.id).first_or_404()
    try:
        db.session.delete(task)
        db.session.commit()
        flash('Task deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting task: {e}', 'error')
    return redirect(url_for('index'))

# --- Auth Routes ---

# UPDATED: Register Route (No Email)
@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    form = RegistrationForm()
    if form.validate_on_submit():
        # Create user without email
        user = User(username=form.username.data)
        user.set_password(form.password.data) # Hash password
        db.session.add(user)
        try:
            db.session.commit()
            flash(f'Account created for {form.username.data}! You can now log in.', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error creating account: {e}', 'error')
    return render_template('register.html', title='Register', form=form)

# UPDATED: Login Route (Uses Username)
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    form = LoginForm()
    if form.validate_on_submit():
        # Find user by username instead of email
        user = User.query.filter_by(username=form.username.data).first()
        # Check user exists and password is correct
        if user and user.check_password(form.password.data):
            login_user(user)
            next_page = request.args.get('next')
            flash('Login Successful!', 'success')
            return redirect(next_page) if next_page else redirect(url_for('index'))
        else:
            flash('Login Unsuccessful. Please check username and password.', 'danger')
    return render_template('login.html', title='Login', form=form)

# Logout Route (No changes needed)
@app.route('/logout')
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))


# --- Create Database Tables ---
with app.app_context():
    db.create_all()

# --- Run Application ---
if __name__ == '__main__':
    app.run(debug=True)