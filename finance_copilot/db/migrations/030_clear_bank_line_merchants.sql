-- Migration 030: a bank line is not a merchant name.
-- Small LLMs echoed the description ("UPI/NAME/1207.../self") as the merchant,
-- and confirming such a label made learned rules copy it onto every later
-- payment. The LLM response model now drops these; clear the stored ones so the
-- payee key is shown instead.
UPDATE annotations SET merchant = NULL WHERE merchant LIKE '%/%/%';
