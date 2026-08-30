-- Seed Indian personal finance category taxonomy.
-- All inserts are idempotent (INSERT OR IGNORE with deterministic IDs).

-- Top-level categories
INSERT OR IGNORE INTO categories (id, name, parent_id) VALUES
    ('cat_food_dining',       'Food & Dining',      NULL),
    ('cat_shopping',          'Shopping',           NULL),
    ('cat_transport',         'Transport',          NULL),
    ('cat_bills_utilities',   'Bills & Utilities',  NULL),
    ('cat_housing',           'Housing',            NULL),
    ('cat_health',            'Health',             NULL),
    ('cat_entertainment',     'Entertainment',      NULL),
    ('cat_travel',            'Travel',             NULL),
    ('cat_education',         'Education',          NULL),
    ('cat_financial',         'Finances',           NULL),
    ('cat_investments',       'Investments',        NULL),
    ('cat_personal_care',     'Personal Care',      NULL),
    ('cat_gifts_donations',   'Gifts & Donations',  NULL),
    ('cat_transfers',         'Transfers',          NULL),
    ('cat_income',            'Income',             NULL),
    ('cat_uncategorized',     'Uncategorized',      NULL);

-- Food & Dining subcategories
INSERT OR IGNORE INTO categories (id, name, parent_id) VALUES
    ('cat_food_restaurants',    'Restaurants',    'cat_food_dining'),
    ('cat_food_delivery',       'Food Delivery',  'cat_food_dining'),
    ('cat_food_groceries',      'Groceries',      'cat_food_dining'),
    ('cat_food_cafe',           'Cafe & Snacks',  'cat_food_dining'),
    ('cat_food_alcohol',        'Alcohol',        'cat_food_dining');

-- Shopping subcategories
INSERT OR IGNORE INTO categories (id, name, parent_id) VALUES
    ('cat_shop_online',      'Online Shopping',       'cat_shopping'),
    ('cat_shop_clothing',    'Clothing & Apparel',    'cat_shopping'),
    ('cat_shop_electronics', 'Electronics',           'cat_shopping'),
    ('cat_shop_retail',      'General Retail',        'cat_shopping');

-- Transport subcategories
INSERT OR IGNORE INTO categories (id, name, parent_id) VALUES
    ('cat_trans_cab',       'Cab & Auto',         'cat_transport'),
    ('cat_trans_fuel',      'Fuel',               'cat_transport'),
    ('cat_trans_public',    'Public Transport',   'cat_transport'),
    ('cat_trans_parking',   'Parking & Tolls',    'cat_transport');

-- Bills & Utilities subcategories
INSERT OR IGNORE INTO categories (id, name, parent_id) VALUES
    ('cat_bills_electricity', 'Electricity',       'cat_bills_utilities'),
    ('cat_bills_water',       'Water',             'cat_bills_utilities'),
    ('cat_bills_gas',         'Gas',               'cat_bills_utilities'),
    ('cat_bills_internet',    'Internet & Broadband', 'cat_bills_utilities'),
    ('cat_bills_mobile',      'Mobile Recharge',   'cat_bills_utilities'),
    ('cat_bills_dth',         'DTH',               'cat_bills_utilities');

-- Housing subcategories
INSERT OR IGNORE INTO categories (id, name, parent_id) VALUES
    ('cat_housing_rent',        'Rent',                        'cat_housing'),
    ('cat_housing_maintenance', 'Maintenance & Society Charges','cat_housing'),
    ('cat_housing_repairs',     'Home Repairs',                'cat_housing');

-- Health subcategories
INSERT OR IGNORE INTO categories (id, name, parent_id) VALUES
    ('cat_health_pharmacy',   'Pharmacy',              'cat_health'),
    ('cat_health_doctor',     'Doctor & Hospital',     'cat_health'),
    ('cat_health_lab',        'Lab Tests',             'cat_health'),
    ('cat_health_insurance',  'Health Insurance Premium', 'cat_health');

-- Entertainment subcategories
INSERT OR IGNORE INTO categories (id, name, parent_id) VALUES
    ('cat_ent_movies',   'Movies & OTT',      'cat_entertainment'),
    ('cat_ent_gaming',   'Gaming',            'cat_entertainment'),
    ('cat_ent_events',   'Events & Concerts', 'cat_entertainment');

-- Travel subcategories
INSERT OR IGNORE INTO categories (id, name, parent_id) VALUES
    ('cat_travel_flights', 'Flights', 'cat_travel'),
    ('cat_travel_hotels',  'Hotels',  'cat_travel'),
    ('cat_travel_train',   'Train',   'cat_travel'),
    ('cat_travel_bus',     'Bus',     'cat_travel');

-- Education subcategories
INSERT OR IGNORE INTO categories (id, name, parent_id) VALUES
    ('cat_edu_fees',    'Tuition & Fees',    'cat_education'),
    ('cat_edu_books',   'Books & Stationery','cat_education'),
    ('cat_edu_courses', 'Online Courses',    'cat_education');

-- Finances subcategories (EMIs, insurance, credit card, tax)
INSERT OR IGNORE INTO categories (id, name, parent_id) VALUES
    ('cat_fin_insurance',    'Insurance Premium',    'cat_financial'),
    ('cat_fin_loan_emi',     'Loan EMI',             'cat_financial'),
    ('cat_fin_cc_payment',   'Credit Card Payment',  'cat_financial'),
    ('cat_fin_tax',          'Tax Payment',          'cat_financial');

-- Investments subcategories (SIPs, mutual funds)
INSERT OR IGNORE INTO categories (id, name, parent_id) VALUES
    ('cat_inv_mf_sip',       'Mutual Fund SIP',      'cat_investments');

-- Personal Care subcategories
INSERT OR IGNORE INTO categories (id, name, parent_id) VALUES
    ('cat_pc_salon', 'Salon & Spa',   'cat_personal_care'),
    ('cat_pc_gym',   'Gym & Fitness', 'cat_personal_care');

-- Gifts & Donations subcategories
INSERT OR IGNORE INTO categories (id, name, parent_id) VALUES
    ('cat_gift_charity',   'Charity',        'cat_gifts_donations'),
    ('cat_gift_religious', 'Religious',       'cat_gifts_donations'),
    ('cat_gift_personal',  'Personal Gifts', 'cat_gifts_donations');

-- Transfers subcategories
INSERT OR IGNORE INTO categories (id, name, parent_id) VALUES
    ('cat_tx_peer',   'Peer Transfer',   'cat_transfers'),
    ('cat_tx_self',   'Self Transfer',   'cat_transfers'),
    ('cat_tx_atm',    'ATM Withdrawal',  'cat_transfers');

-- Income subcategories
INSERT OR IGNORE INTO categories (id, name, parent_id) VALUES
    ('cat_inc_salary',          'Salary',               'cat_income'),
    ('cat_inc_freelance',       'Freelance',             'cat_income'),
    ('cat_inc_interest',        'Interest & Dividends',  'cat_income'),
    ('cat_inc_refund',          'Refund',                'cat_income'),
    ('cat_inc_cashback',        'Cashback',              'cat_income'),
    ('cat_inc_reimbursement',   'Reimbursement',         'cat_income'),
    ('cat_inc_opening_balance', 'Opening Balance',       'cat_income');
