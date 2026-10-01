## Building a table view

A `table` view is `spec.columns` (a list of names) and `spec.rows` (a list of lists, in column order).

- Set the columns first, then add rows in batches with `view_apply_ops`: `{"op":"add","path":"spec.columns","value":["City","Population"]}` then `{"op":"add","path":"spec.rows","value":[["Berlin",3769000],["Hamburg",1841000]]}`.
- Keep numbers as numbers (not formatted strings) so the table sorts and aligns them; put units in the column name.
- Order the columns so the first one identifies the row and the ones the question is about come next. A table with more than a dozen columns is two tables.
- A `text` control bound to a filter param and a `select` on a sort column are the controls a user wants.
