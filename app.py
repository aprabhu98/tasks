import os
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_, func # Import 'or_' and 'func'
from datetime import date, datetime # Import 'date' for parsing

# --- Configuration ---
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_NAME = 'tasks.db'
instance_path = os.path.join(BASE_DIR, 'instance')
DB_URI = f'sqlite:///{os.path.join(instance_path, DB_NAME)}'

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_very_secret_key_here_change_me' # IMPORTANT: Change this!
app.config['SQLALCHEMY_DATABASE_URI'] = DB_URI
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Create instance folder if it doesn't exist BEFORE initializing db
os.makedirs(instance_path, exist_ok=True)

db = SQLAlchemy(app)

# --- Database Models ---

# Association table
task_tags = db.Table('task_tags',
    db.Column('task_id', db.Integer, db.ForeignKey('task.id'), primary_key=True),
    db.Column('tag_id', db.Integer, db.ForeignKey('tag.id'), primary_key=True)
)

class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_complete = db.Column(db.Boolean, default=False)
    tags = db.relationship('Tag', secondary=task_tags, lazy='subquery',
                           backref=db.backref('tasks', lazy=True))

    def __repr__(self):
        return f'<Task {self.id}: {self.title}>'

class Tag(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)

    def __repr__(self):
        return f'<Tag {self.id}: {self.name}>'

# --- Helper Functions ---

def get_or_create_tag(tag_name):
    """Gets an existing tag or creates a new one. Handles commit within the function."""
    tag_name = tag_name.strip().lower()
    if not tag_name:
        return None
    tag = Tag.query.filter_by(name=tag_name).first()
    if not tag:
        tag = Tag(name=tag_name)
        db.session.add(tag)
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            flash(f"Error creating tag '{tag_name}': {e}", "error")
            tag = Tag.query.filter_by(name=tag_name).first() # Try fetching again
            if not tag: return None
    return tag

def process_tags(tag_string):
    """Processes a comma-separated string of tags."""
    if not tag_string:
        return []
    tag_names = [name.strip() for name in tag_string.split(',') if name.strip()]
    tags = []
    for name in tag_names:
        tag_object = get_or_create_tag(name)
        if tag_object:
            tags.append(tag_object)
    return tags

# --- Context Processors ---

@app.context_processor
def inject_now():
    """Injects the current UTC datetime into the template context."""
    return {'now': datetime.utcnow()}

# --- Routes ---

@app.route('/', methods=['GET'])
def index():
    """Displays tasks, filtered by tag, status, search term, and creation date."""
    filter_tag = request.args.get('tag')
    filter_status = request.args.get('status')
    search_term = request.args.get('search')
    filter_date_str = request.args.get('created_on') # Get date string

    query = Task.query
    selected_date_obj = None # Initialize

    # --- Apply Filters ---
    # Search Filter
    if search_term:
        like_pattern = f'%{search_term}%'
        query = query.filter(
            or_(
                Task.title.ilike(like_pattern),
                Task.description.ilike(like_pattern),
                Task.tags.any(Tag.name.ilike(like_pattern))
            )
        )

    # Tag Filter
    if filter_tag:
        tag = Tag.query.filter_by(name=filter_tag).first()
        if tag:
            query = query.filter(Task.tags.contains(tag))
        # No warning flash needed here as other filters might still apply

    # Status Filter
    if filter_status == 'complete':
        query = query.filter_by(is_complete=True)
    elif filter_status == 'incomplete':
        query = query.filter_by(is_complete=False)

    # Date Filter (Created On)
    if filter_date_str:
        try:
            selected_date_obj = date.fromisoformat(filter_date_str)
            # Filter where the DATE part of created_at matches the selected date
            query = query.filter(func.date(Task.created_at) == selected_date_obj)
        except ValueError:
            flash(f"Invalid date format for 'Created On'. Please use YYYY-MM-DD.", "error")
            filter_date_str = None # Clear invalid date string if parsing fails

    # --- Fetch Results ---
    tasks = query.order_by(Task.created_at.desc()).all()
    all_tags = Tag.query.order_by(Tag.name).all()

    return render_template('index.html',
                           tasks=tasks,
                           all_tags=all_tags,
                           current_filter_tag=filter_tag,
                           current_filter_status=filter_status,
                           current_search_term=search_term,
                           current_filter_date=filter_date_str # Pass date string back
                          )

@app.route('/add', methods=['POST'])
def add_task():
    """Adds a new task."""
    title = request.form.get('title')
    description = request.form.get('description')
    tags_string = request.form.get('tags')

    if not title:
        flash('Task title is required!', 'error')
        return redirect(url_for('index'))

    new_task = Task(title=title, description=description)
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


@app.route('/update/<int:task_id>', methods=['GET', 'POST'])
def update_task(task_id):
    """Updates an existing task."""
    task = Task.query.get_or_404(task_id)

    if request.method == 'POST':
        new_title = request.form.get('title')
        new_description = request.form.get('description')
        new_tags_string = request.form.get('tags')

        if not new_title:
            flash('Task title cannot be empty!', 'error')
            tags_str = ", ".join(tag.name for tag in task.tags)
            return render_template('update_task.html', task=task, tags_string=tags_str)

        task.title = new_title
        task.description = new_description
        # updated_at is handled by onupdate=datetime.utcnow

        task.tags.clear()
        new_tags = process_tags(new_tags_string)
        task.tags.extend(new_tags)

        try:
            db.session.commit()
            flash('Task updated successfully!', 'success')
            return redirect(url_for('index'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating task: {e}', 'error')
            tags_str = new_tags_string
            return render_template('update_task.html', task=task, tags_string=tags_str)

    # GET Request
    tags_str = ", ".join(tag.name for tag in task.tags)
    return render_template('update_task.html', task=task, tags_string=tags_str)


@app.route('/toggle/<int:task_id>', methods=['POST'])
def toggle_complete(task_id):
    """Toggles the completion status of a task."""
    task = Task.query.get_or_404(task_id)
    task.is_complete = not task.is_complete
    try:
        db.session.commit()
        status = "completed" if task.is_complete else "incomplete"
        flash(f'Task marked as {status}.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating task status: {e}', 'error')

    # Redirect back to the previous view, preserving query parameters
    return redirect(request.referrer or url_for('index'))


@app.route('/delete/<int:task_id>', methods=['POST'])
def delete_task(task_id):
    """Deletes a task."""
    task = Task.query.get_or_404(task_id)
    try:
        db.session.delete(task)
        db.session.commit()
        flash('Task deleted successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting task: {e}', 'error')

    # Redirect to plain index after delete is usually safest
    return redirect(url_for('index'))

# --- Create Database Tables ---
with app.app_context():
    db.create_all()

# --- Run Application ---
if __name__ == '__main__':
    app.run(debug=True) # Set debug=False in production