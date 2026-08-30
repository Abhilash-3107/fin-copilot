-- Add Reimbursement as an Income subcategory
INSERT OR IGNORE INTO categories (id, name, parent_id) VALUES
    ('cat_inc_reimbursement', 'Reimbursement', 'cat_income');
