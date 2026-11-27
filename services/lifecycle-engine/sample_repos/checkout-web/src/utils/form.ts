// Not Flagsmith: a form helper that happens to have a getValue() method.
interface Form {
  getValue(field: string): string;
}

export function email(form: Form): string {
  return form.getValue("email");
}
