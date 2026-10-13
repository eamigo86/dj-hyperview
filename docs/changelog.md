# Changelog

This page records user-visible release changes, including unpublished attempts.

## Unreleased

### Security

- Reject an element with more than 128 attributes, more than 8 in-scope namespaces,
  or more than 32 namespace declarations on the path from the root to it, before XSD
  validation, using the new stable validation code `max_attributes`. Validation time
  grew faster than linearly with these counts, so a few kilobytes of untrusted
  markup could occupy a worker for seconds. The schema validator rebuilds an
  element's namespace map from all of its ancestors several times, and once more for
  each in-scope prefix when the element declares a namespace, so the cost grows with
  the declarations on a path times the prefixes in scope. The path cap counts every
  declaration, including a prefix rebound at each level. Looser caps would not do:
  at 64 prefixes and 256 declarations, 20,000 elements that each rebind a prefix
  below a path at the cap take about 28 s at the default limits, against about 1.1 s
  with these caps and about 0.6 s for 20,000 plain elements. The widest element the
  bundled schema declares has 126 attributes. The HyperTodo templates, with their
  includes expanded, use at most 13 attributes on an element, 5 namespace
  declarations on a path and 2 prefixes in scope, and sibling elements do not add
  up. Valid Hyperview markup has no reason to exceed a cap, and the caps are fixed,
  so a document that does must be restructured. Checking them takes time
  proportional to the number of nodes and declarations. They do not bound the whole
  document: `VALIDATION.MAX_BYTES`, `MAX_NODES` and `MAX_DEPTH` remain the global
  limits, a large valid document still costs one to two seconds of CPU on a fast
  development machine, and untrusted input should lower them.
- Schema validation now stops at the first violation. xmlschema's default (lax)
  mode builds an error for every violation in the whole tree before it reports the
  first, and an undeclared attribute costs time proportional to the number of
  attributes on its element. Rejecting 300 elements with 256 undeclared attributes
  each therefore took about 7.7 s, where a valid document of the same size takes
  under a second. The reported violation is the first one xmlschema finds, with the
  same message and line, which is not always the first in document order: it reports
  a parent's content-model error, such as a missing required child, after the errors
  inside its children. One classification changed. A document that has a violation
  and, after it, an `xsi:type` naming no declared type used to be rejected as
  `schema_invalid`, because the lax mode failed on the type before it returned
  anything; it is now rejected with the earlier `schema` violation. A document whose
  only problem is such an `xsi:type` is still `schema_invalid`. Validating a valid
  document costs the same as before.
- Stop echoing rejected template names from an ordered selection. When every
  candidate misses, `TemplateNotFound` now lists only valid names that were not
  found, so control characters and escape sequences in hostile names cannot reach
  logs.
- The Admin **Format and Validate** check, which the Admin form repeats before every
  save when `ADMIN.EDITOR` is on, now takes time proportional to the size of the
  draft. It used to lex the whole draft again for every Django token, and to rescan
  the rest of the input for every `{{`, `{%`, `<?xml` or `<!--` that had no closer.
  An authorized user could therefore occupy a worker, and hold the row lock of a
  save, for tens of seconds: 4,000 tokens took 25 s, `"{{\n" * 40000` (120 KB)
  30 s, `"<?xml " * 40000` (240 KB) 32 s, and `"{{ a }}" + "<!--" * 40000`
  (160 KB) 49 s. They now take well under a second. The check also compiles the
  draft with a lexer that finds the same tokens as Django's in linear time: Django's
  lazy pattern reads on to the end of the line for every `{{`, `{%` or `{#` without
  a closer, so 40,000 of them on one line (120 KB) took 10 s in its lexer alone.
  Literal `{% include %}` targets are now read from their source and expanded with
  that lexer. Before, each target was also compiled by Django's `Template`, so a
  draft with one include of 40,000 `{{ ` took 10 s and four such includes took
  42 s; they now take 0.3 s. The composed source is bounded as well: expansion
  stops, with the informational note `schema_static_incomplete`, once the draft and
  its includes together exceed `VALIDATION.MAX_BYTES`, counted in UTF-8 bytes as the
  limit is everywhere else. A draft with 64 includes of
  a 990 KB template used 3.3 GB and now uses 79 MB.
- Limit the work that unclosed template delimiters cost when a stored template is
  compiled, using the new stable validation code `max_unclosed`. Django's lexer
  reads on to the end of the line for every `{{`, `{%` or `{#` that closes no tag,
  so a stored template of `"<view>" + "{{ " * 40000` (120 KB, far below
  `VALIDATION.MAX_BYTES`) passed validation and then occupied a request worker for
  10.5 s every time Django compiled it. Publication, model validation, the Admin
  check (including the targets of literal includes) and the template loader now
  count those characters in linear time, as the sum over every opener that closes
  no tag of the characters up to the end of its line, and reject a source above 10
  million. The count tracks what Django's lexer reads, so the worst accepted source
  costs it about a tenth more than the same source without the stray openers,
  measured on 1,000,000 bytes of ordinary tags. The limit is fixed and does not grow
  with `MAX_BYTES`. The loader applies it before it hands any source to Django, so a
  stored row that predates the limit, or that was written with `bulk_create()` or
  `save()`, fails when it is served instead of occupying a worker. Filesystem
  templates are developer-owned and no user can write them; they pass through the
  same loader check, which only a deliberately pathological file reaches. See
  [Security](security.md).
- `sse_response` now sends `Cache-Control: no-store, no-transform` instead of
  `no-cache, no-transform`. The stream is per authenticated user, and `no-cache`
  still lets a shared cache store the response and revalidate it later.

### Breaking changes

Each entry changes behavior that existing code, configuration or templates may rely
on, and says how to migrate.

- Custom template sources are no longer cached unless they declare
  `cacheable = True`. Before, the resolver cached every source that lacked
  a private opt-out marker, keyed only by the source class and its constructor
  options. A source whose content depends on ambient state, such as a `ContextVar`
  that holds the current tenant, therefore served one caller's content to another
  once `HYPERVIEW["CACHE"]` was enabled. `FileSystemSource` and `DatabaseSource`
  with an explicit `using` alias keep their caching, but only while `resolve()` is
  their own: a subclass that overrides `resolve()`, for example to select a
  tenant's directory, is no longer cached until it sets `cacheable = True`. To keep
  caching a custom source, set `cacheable = True` on it, but only when its output
  depends solely on the template name and the constructor options; see
  [Cache consistency](cache-consistency.md#choose-which-sources-are-cached). The
  private `_dj_hyperview_cacheable` marker is removed and no longer has any effect.
- `publish_template()` no longer re-activates an inactive template. Its `active`
  argument now defaults to `None`, which keeps an existing template's value and
  publishes a new template as active; before, the default `True` overwrote the
  stored value. **A caller that relied on republishing to activate a template must
  now pass `active=True`.**
- The resolver now checks what a source returns, with or
  without a cache. A `ResolvedTemplate` must be named exactly as requested and all
  of its fields must be `str`; otherwise the resolver raises `SourceUnavailable`
  (reason `invalid resolved template`) in both failure modes. Before, such a result
  was written to the cache and then rejected on every read: `raise` mode failed on
  the second lookup with `invalid payload`, `bypass` mode never cached it, and a
  field that is not JSON serializable raised `TypeError` even in `bypass` mode. The
  built-in sources already comply. A custom source that answered to another name,
  or left one of the five fields as `None`, bytes or an integer, must be fixed. A
  subclass of `ResolvedTemplate` may still add fields of its own: they are not
  checked, an uncached lookup returns the subclass instance, and the cache stores,
  and later returns, only the five contract fields as a plain `ResolvedTemplate`.
- The system check now compiles the merged `EXTRA_SCHEMAS`
  registry. It compiled each extra schema alone, so two schemas that declare the
  same element differently, or an extra schema that declares a Hyperview or alert
  name differently from the bundled schemas, passed it. The conflict then surfaced
  as `duplicate_schema_declaration` from every validation, which recompiled the
  registry each time (70 to 90 ms, never cached). After every file passes on its
  own, the check compiles the registry the first request would use, once, and
  reports a failure as `dj_hyperview.E012` against the whole setting, with
  `reason: duplicate_schema_declaration`, or `reason: schema_invalid` for any other
  failure of the compiler, including one it does not translate. A registry that
  compiles cleanly is cached, so the first request does not compile it again, and a
  project without `EXTRA_SCHEMAS` pays nothing. The merge is skipped while
  `SCHEMA_EXTENSIONS` has errors (`dj_hyperview.E021`). **A project whose extra
  schemas conflict now fails system checks, and every call that reads the settings
  raises `HyperviewConfigurationError`, a subclass of `HyperviewError` and
  `ImproperlyConfigured` that is neither a `TemplateValidationError` nor a
  `ValueError`.** These returned normally before: `get_settings()`,
  `validate_template_source`, `HyperviewEngine()`, `resolve_template`,
  `TemplateResolver.from_settings()`, `TemplateCache.from_settings()`,
  `FileSystemSource()`, and `invalidate_templates()` when `CACHE` is set. These
  raised `TemplateValidationError` before: `validate_hxml`, `validate_fragment_hxml`,
  `validate_hyperview_schema`, the response classes, `HyperviewTemplateView`,
  `render_template` once it validates a rendered document, and
  `get_hyperview_catalog`. Code that handles
  `TemplateValidationError` around them must handle `HyperviewConfigurationError` as
  well. A schema file that starts to conflict after the settings were first read
  still raises `TemplateValidationError` from the call that notices it.
- Unknown `HYPERVIEW` keys are now configuration errors (`dj_hyperview.E023`)
  instead of being ignored. The rule covers the top level, `SOURCES` entries,
  `CACHE`, `VALIDATION` and `ADMIN`, so a typo such as `MAX_BYTE`, `FAILURE_MODES`
  or `PERMISION` can no longer silently fall back to a default. **A project with a
  stray key now fails system checks and `get_settings()`**; correct or remove the
  key. The removed `SCHEMA_PROFILE`, `VALIDATION.MODE` and `VALIDATION.SCHEMA`
  keep their existing errors and are not reported twice.
- Reject format characters, and line and paragraph separators, in template names.
  The rule is a denylist of Unicode categories: Cf (format characters, such as
  bidirectional overrides and isolates, zero-width characters, the soft hyphen, the
  byte order mark, and tag characters), Zl and Zp (the line and paragraph
  separators U+2028 and U+2029) now raise `InvalidTemplateName`, like Cc (control
  characters) and Cs (surrogates) already did. It is not a general test for
  invisible or confusable names: space separators (Zs, such as U+00A0 or U+3000)
  and letters or symbols that merely look blank, such as U+3164 HANGUL FILLER or
  U+2800 BRAILLE PATTERN BLANK, are still accepted. The rejected characters could
  make two names look identical, or hide a file extension, in logs and error pages.
  The rule is shared by the resolver, the engine, the cache and signal helpers, and
  the database source, model validator and Admin editor. **A template whose name
  uses one of these characters, for example a zero-width joiner in an emoji
  sequence, must be renamed.** `check_hyperview_templates` reports a stored row
  with such a name as `invalid_name`, and the row is no longer resolved.
- Reject any `..` segment, any non-printable character, and the characters `?`,
  `#` and `:` in a schema reference of an `EXTRA_SCHEMAS` file with the stable
  `forbidden_schema_reference` code (`dj_hyperview.E012` during configuration
  checks), and read the references of a symlinked schema file from the symlink's
  own directory. The dependency guard resolved symlinks physically while the XSD
  compiler folded `..` lexically, so a reference such as `link/../../outside.xsd`
  through a symlinked directory passed the guard and made the compiler load a file
  outside the configured root. The compiler's URL parser also drops tabs and
  carriage returns, so `.<TAB>.` passed as an ordinary directory name while the
  compiler read `..`. It drops an empty `?` or `#` and reads a first segment such as
  `1:.` as a drive, so `x.xsd?` named a benign decoy file `x.xsd?` for the guard and
  the file `x.xsd`, a symlink to a schema outside the root, for the compiler.
  And the guard joined the references of a schema file that is a symlink onto the
  directory of its target, where the compiler joins them onto the directory of the
  link, so `out/evil.xsd` could name a decoy for the guard and a file outside the
  root for the compiler. A `schemaLocation` that starts with `<`, which the XSD
  compiler reads as inline XML rather than as a path, is rejected by the `:` rule,
  because any inline schema declares a namespace; it was reported as
  `schema_invalid`. **A schema that relied on `..` inside its own root, for
  example `components/../shared.xsd`, must now use the plain path `shared.xsd`. A
  schema file whose name contains `?`, `#` or `:`, such as `a#b.xsd`, must be
  renamed. A schema file that is a symlink now finds its dependencies beside the
  link, as the compiler does, not beside its target.**
- The Admin **Format and Validate** check now applies the limits that rendering
  applies before any schema work. It rejects a template whose source has more than
  `VALIDATION.MAX_NODES` nodes or `MAX_DEPTH` levels, an element with more than 128
  attributes or 8 in-scope namespaces, or a path with more than 32 namespace
  declarations, using the stable codes `max_nodes`, `max_depth` and
  `max_attributes`, and it returns at most 50 diagnostics, errors first, followed by
  an informational `diagnostics_truncated` entry when more were found. Before, 1,008
  namespace declarations on one path took 10 s, and 80,000 undeclared elements
  produced 80,000 diagnostics and a 16 MB response. **The source of a template
  counts every branch of a conditional, so a template whose branches together
  exceed a limit now fails the check although each rendering would pass. Raise the
  limit if you need such a template.**
- `select_template()` called with a bare string now raises `TypeError`, as Django
  does, instead of iterating its characters as template names. **Pass a list or a
  tuple of names, or use `get_template()` for one name.**
- The Admin schema catalog endpoint (`hxml-catalog/`) now answers 404 unless
  `HYPERVIEW["ADMIN"]["EDITOR"]` is enabled, as the validation endpoint already
  did. Before, it served the catalog, including the names from the project's
  `EXTRA_SCHEMAS` and `SCHEMA_EXTENSIONS`, to every staff user with view
  permission while the editor was off. **An integration that fetched the catalog
  with the editor off must enable the editor.**
- `X-Hyperview-Version` is treated as a claimed value. A request still counts as
  Hyperview whenever the header is present, but `HyperviewRequestDetails.version`
  is now `None` unless the value matches `[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}`. It
  used to return any string verbatim, including empty or oversized values. **Code
  that reads `version` must accept `None`.**
- Reaching the limit of 64 expanded literal includes is now the informational note
  `schema_static_incomplete` and no longer the error `django_include_limit`, which
  blocked the save of a template that was only too large to check completely.
  Expansion stops at the limit, and the code `django_include_limit` is no longer
  produced. **Tooling that matched `django_include_limit` must expect
  `schema_static_incomplete`, with severity `info`.**
- The `charset` argument of a response, and the `charset` parameter of its
  `Content-Type`, now follow the rule that decides the encoding name of an XML
  declaration (see *Fixed*): the name is read from the alias table of Python's
  `encodings` package and never looked up in `codecs`. A spelling that only the
  lookup's own normalization accepted, such as `utf 8` or a trailing space, is now
  rejected with the usual `ValueError("Hyperview responses require UTF-8")`. So is
  a name that contains a NUL or a lone surrogate, which used to let the lookup's
  own `ValueError` escape with another message. **Pass a standard spelling such as
  `utf-8`.**
- Template source is now rejected with the stable code `max_unclosed` when the
  unclosed `{{`, `{%` and `{#` delimiters on its lines make Django's lexer read more
  than 10 million characters (see *Security*). Such a source was accepted before,
  and every compile of it took seconds. Only a very long line of stray openers
  reaches the limit: about 2,600 `{{ ` in a row (8 KB on one line) do, and a
  template with line breaks, or one whose delimiters close, never does. **A stored
  or filesystem template that reaches it must close the delimiters, spell them with
  `{% templatetag openvariable %}` and its siblings, or break the line; a stored
  row that reaches it fails when it is served.**
- The database template admin no longer strips `content`: leading and trailing
  whitespace, including a final newline, is now stored exactly as submitted. Django's
  form field stripped it, so every admin save silently rewrote a template that
  ended in a newline. With the editor off, content that is only whitespace is
  therefore saved; the empty string is still rejected as required, and with the
  editor on the source check still rejects whitespace-only content. **An integration
  that relied on the form trimming `content`, or on it rejecting whitespace-only
  content with the editor off, must do that itself.**
- A Hyperview `Content-Type` parameter whose value needs quoting, such as
  `profile="mobile app"`, is now written back quoted and escaped. Before, the
  rebuilt header lost the quotes (`profile=mobile app`), which is not a valid
  parameter. A parameter name that is not an RFC 9110 token or ends in `*` (an
  RFC 8187 extended name such as `a*=x`), or a value with a control character, a
  character above U+00FF, or a trailing backslash, now raises `ValueError` instead
  of producing a malformed header. Django and `email` read the quote after a
  trailing backslash as escaped, so such a value would come back changed and
  swallow the next parameter, and Django drops the star of an extended name and
  decodes its value, so `a*=x` would come back as `a=x`. The check splits the header
  the way Django does, so a stray quote cannot hide a star from it. A header that
  Django cannot parse, such as an extended value with an unknown encoding (which
  Django 5.2 reports as `LookupError`), raises the same `ValueError` on both
  supported Django versions, and so does a `Content-Type` longer than 10,000
  characters, which Django 6.1 already refused and Django 5.2 read without a cap
  (1 MB of semicolons took about 15 s). **A caller that
  passes such a parameter must drop it or give it a value that fits: printable
  Latin-1 characters, and no backslash at the end.**
- Source that nests blocks more than 100 levels deep, such as `{% if %}`, no longer
  fails the Admin validation request and the Admin save with HTTP 500, and can no
  longer be saved only to fail with `RecursionError` when it is served. Django's
  parser compiles about 490 nested `{% if %}` blocks, but rendering recurses three
  frames for each and fails at about 330 in an empty stack and at about 230 below
  300 frames of caller, where a view in a plain Django request was measured 8 to 24
  frames deep, so the earlier check accepted source that could not render. The
  check, the Admin form, `publish_template` and model validation report it as
  `too_deep`, "Template is nested too deeply." The levels are counted while the
  source is parsed, so an empty innermost block counts, and a filter in an opening
  tag, such as `{% if a|upper %}`, no longer turns the failure at about 490 levels
  into a `TypeError` and HTTP 500. For a literal include target the Admin reports
  `too_deep` for the target. The limit applies to each template on its own, so
  chained includes can still nest more deeply than can be rendered. **A stored
  template that nests blocks more than 100 levels deep is now rejected when it is
  saved; flatten it.**
- The source check for forbidden declarations now agrees with Django about three
  constructs. A `{% comment %}` inside a `{% verbatim %}` body hides nothing, so a
  declaration there is rejected when the template is saved, as it was already when
  it was served. A comment ends at the first tag named exactly `endcomment`, so a
  declaration after `{% endcomment note %}` is hidden and the template is accepted,
  as it renders. A tag whose delimiters are split over a line break, such as `{%`,
  a newline and `comment %}`, is plain text in Django and now hides nothing either.
  **A template that hid a declaration in a `{% comment %}` inside a `{% verbatim %}`
  body, or after a comment tag split over a line break, is now rejected when it is
  saved; remove the declaration.**

### Added

- A public, optional `cacheable` attribute on template sources. It defaults to
  `False`; `FileSystemSource.cacheable` is `True` and `DatabaseSource.cacheable`
  is `True` only with an explicit `using` alias, each only while the instance uses
  the built-in `resolve()`. Both can be assigned, for example `self.cacheable =
  False` in a subclass constructor, or overridden by a class attribute. On an
  instance they behave like a class attribute: deleting an assignment, or patching
  with `unittest.mock.patch.object`, restores the default. The `TemplateSource`
  protocol still requires only `resolve()`.
- `dj_hyperview.validators.validate_xml_text`, a Django validator (error code
  `xml_characters`) that rejects characters XML 1.0 cannot represent.
- `hv_text` and `hv_attr` template filters that remove those characters and
  escape element text or attribute values. `hv_attr` also encodes line feeds,
  carriage returns and tabs so they survive XML attribute-value normalization.
- `CACHE.FAILURE_MODE = "bypass"` is no longer silent. Each cache failure it
  swallows logs a `WARNING` on the new `dj_hyperview.cache` logger, such as
  `template_cache_bypassed operation=read reason=invalid payload`, with fixed codes
  only: never a template name, cache key, source identity or backend message. A
  source that declares `cacheable = True` but whose constructor options the cache
  cannot identify is logged once at `INFO` as `template_source_uncacheable`. A
  working cache logs nothing. Before, a backend that failed at runtime, or a payload
  that could not be read back, left no trace; a wrong alias or an unsupported
  backend was already a system-check error (`dj_hyperview.E004`, `E018`).

### Changed

- `ADMIN.PERMISSION` is called at most once per request. Every add, change and
  delete check of one request now shares the first result, which is also kept when
  the callback raises, so the failure is logged once. A page used to call the
  callback eight times on the changelist. The next request calls it again. Only a
  `bool` counts as the shared result, so a request object that invents attributes,
  such as a mock, still runs the callback.
- The database admin changelist no longer loads the template bodies; the change and
  delete pages still do.
- The after-commit realtime publisher no longer builds a Redis client for every
  hint. The worker keeps one connection per configuration, replaces it after a
  failure, after 30 seconds without use, or after five minutes of age under steady
  traffic so that the host name is resolved again, and still sends the channels of
  a hint in one pipeline round trip. A short circuit breaker also stops each commit
  from waiting for the connection timeout while Redis is down: after three
  consecutive failures of a configuration, hints for it are dropped immediately for
  five seconds, and the next hint probes the server. A client that cannot be built
  counts as a failure. The breaker logs the fixed code `realtime_publish_circuit_open`
  once when it opens, not once per dropped hint. Publication stays best-effort and
  at-most-once, and a failure still never fails the committing request. The log
  carries no payload, topic or URL. If the client raises a `BaseException`, such as
  a gevent `Timeout`, which ends the worker thread, the next commit starts a new
  worker instead of waiting for its full timeout, and the ended worker releases
  the commits queued behind it, which would otherwise wait out their own timeouts,
  before it closes its clients, so a client that fails to close cannot hold them.
- A failed realtime publication now logs the exception type name as well:
  `realtime_publish_failed error_type=<TypeName>`. The exception message is still
  never logged because it can contain the Redis URL.
- The new system check `dj_hyperview.W007` warns when `HYPERVIEW["REALTIME"]` is
  configured and redis-py is not installed, with the hint
  `pip install "dj-hyperview[realtime]"`. Before, a missing extra passed the checks
  and only showed up at runtime as a fixed `realtime_publish_failed` log line. The
  check looks the package up without importing it.
- `SOURCES[].OPTIONS` is bound to the backend constructor during system checks.
  Options the constructor does not accept, or required arguments it does not
  receive, produce `dj_hyperview.E024`. Before, such a project passed the checks
  and raised `TypeError` on every request. On Python 3.14, where annotations are
  evaluated lazily, the check reads the constructor signature without resolving
  annotations; a name imported only under `TYPE_CHECKING` would otherwise raise
  `NameError` and silently disable the check for that backend.
- `requires-python` is now `>=3.12` with no upper bound. The previous `<3.15` cap
  stopped installers on Python 3.15 from installing the package at all. Python
  3.12, 3.13 and 3.14 remain the tested set, declared by the classifiers and the
  CI matrix; newer versions are not blocked by the package metadata but are not
  tested yet. `uv.lock` was regenerated and now also records Python 3.15 wheels.
- `dj_hyperview.E012` now ends with the cause, such as `(reason: schema_invalid)`,
  and `dj_hyperview.E003` and `dj_hyperview.E017` add `(import raised
  ModuleNotFoundError)` when the dotted path could not be imported. Before, a
  missing file, an invalid schema, a forbidden reference and a DTD all produced the
  same E012 message. The cause is a stable code or an exception class name; file
  paths and exception messages are never included.
- A rendered document that contains a character XML 1.0 cannot represent now fails
  with `malformed_xml` and the message "document contains characters not allowed
  in XML 1.0" instead of the generic "invalid XML". Validation still fails closed
  and the message still does not echo document content. This includes a lone
  surrogate passed to `HyperviewResponse`, `HyperviewFragmentResponse` or
  `write()`. A document that also exceeds `MAX_BYTES`, declares a DTD or entity, or
  declares a non-UTF-8 encoding keeps reporting `max_bytes`,
  `forbidden_declaration` or `invalid_encoding`, with one exception: a lone
  surrogate cannot be encoded, so the validators report `malformed_xml` for it even
  when the document also exceeds `MAX_BYTES` or declares a DTD or entity. A
  non-UTF-8 declaration is still reported first.
- Render validation now removes only XML whitespace (space, tab, carriage return
  and line feed) before the document, after an optional BOM. Before, it also
  dropped other whitespace and control characters silently, such as U+00A0,
  U+0085 or U+001C. A document that starts with one of them now fails with
  `malformed_xml`.

### Fixed

- The editor's validation and completion URLs are now resolved on the `AdminSite`
  that serves the form. Before, they were always looked up in the default `admin`
  namespace, so a model registered on a custom site silently lost its validation
  and completion.
- `content_type=None` on `HyperviewResponse`, `HyperviewTemplateResponse` and the
  fragment classes now yields the Hyperview media type of the class, as the default
  argument does, when `headers` has no `Content-Type`. Before, it fell through to
  Django's default `text/html`. When `headers` carries a `Content-Type` (in any
  letter case), `None` keeps that header, as it does in Django. Omitting the
  argument with such a header still raises Django's `ValueError` about giving the
  type twice, because the default is a content type too.
- A `Content-Encoding` other than `gzip` or `identity` now fails at iteration and
  serialization with the message "only gzip Content-Encoding is supported". Before,
  it reported only that the header does not match the transport, which hid that the
  encoding itself is unsupported. The error code `http_encoding` is unchanged.
- `dj_hyperview.loaders.ResolverLoader` can be named in `TEMPLATES`
  `OPTIONS["loaders"]`. Its `resolver` and `validation` arguments now default to
  the resolver and validation limits built from `HYPERVIEW`; before, Django's
  construction of the loader from its dotted path raised `TypeError`.
- The engine behind `render_template` and the template responses is rebuilt when
  `DEBUG` changes, as Django rebuilds its own engines, instead of keeping the debug
  option it first saw.
- A `TEMPLATES` entry without a `BACKEND`, or with `BACKEND` set to `None`, no
  longer makes `HyperviewEngine` fail with `AttributeError`. The engine skips it,
  like an entry whose backend cannot be imported, and reads its options from the
  first Django backend.
- Admin **Format and Validate** now treats a Django delimiter the way Django's lexer
  does. A `{% comment %}` block is ignored whatever follows its name, as in
  `{% comment "temporarily disabled" %}`: the check looked for
  `{% endcomment "temporarily disabled" %}`, which Django never needs, so the
  commented-out markup was validated as live and could block a save of a template
  that renders. A `{# ... #}`, `{{ ... }}` or `{% ... %}` that spans a line break is
  literal text, as it is when the template is served, so the check now reports what
  that text does to the document. A `{# note #}` comment with a line break inside it
  between two elements used to pass the check, and the rendered document then failed
  schema validation. The tokens are the ones Django's lexer produces, and the check
  classifies all of them in a single pass instead of lexing the draft again for
  each one.
- A variable that applies the `safe` filter in element text, such as
  `{{ fragment|safe }}` in an element that allows only elements, no longer fails the
  Admin check. It may render markup, so its position is now reported with the
  informational note, like other runtime-dependent structure. Variables without
  `safe` still count as text, as before.
- The Admin check no longer counts the namespace declaration and prefix of its own
  synthetic root toward the namespace limits. A document with exactly 32
  declarations on a path or 8 in-scope namespaces passed at render time and failed
  in the Admin.
- A `{% verbatim %}` body that supplies markup, such as the required child of an
  element or half of a tag, now gets the informational note in the Admin check.
  It used to be reported as the blocking `schema_structure` or `xml_syntax` error,
  although the template renders.
- Publication and model validation compile stored source with the same linear lexer
  as the Admin check, so a long line of unclosed `{{` is checked in linear time
  instead of the 10 s that Django's lexer takes for 40,000 of them (120 KB). How
  much such a line may cost when the template is served has its own limit; see
  *Security*.
- With unusable `HYPERVIEW` settings, including conflicting extra schemas, the Admin
  no longer fails with a server error on the change page. The change page of an
  existing template renders read-only with a plain text area instead of the editor;
  adding, changing and deleting are denied (HTTP 403), as for any invalid setting;
  and the HXML completion endpoint answers with the same `configuration_error` JSON
  diagnostic as the validation endpoint, instead of an unhandled exception. A page
  logs one `WARNING` without a traceback that points to `python manage.py check`;
  only a permission callback that raises is logged as an `ERROR` that names
  `ADMIN.PERMISSION`.
- A `SystemExit`, `KeyboardInterrupt` or other non-`Exception` raised while
  `RedisBroker.subscribe` opens a subscription is no longer converted into
  `RealtimeUnavailable`. The owned clients are still closed first, then the original
  exception propagates, as cancellation already did.
- Saving or deleting a template through a proxy model of `HyperviewTemplate` now
  schedules cache invalidation. Django sends the model signals of a proxy instance
  with the proxy class as sender, so the handlers connected for `HyperviewTemplate`
  alone never ran, and a cached template stayed stale after such a write. The
  handlers are now connected to the model and to each of its subclasses, including
  a proxy defined after startup, and to no other model, so Django keeps its fast
  bulk delete for the rest of the project. A rename or delete also reads the stored
  name through the base manager, so a proxy whose default manager hides the row
  still invalidates the old name.
- Admin **Format and Validate** now formats a template that contains `{% load %}`.
  The tag renders nothing, but a top-level one made the formatter keep the whole
  source and report "HXML was already formatted." The package's own
  `{% hv_csrf_token %}` tag is also accepted; before, a template using it could not
  be formatted at all.
- The Admin deletion page shows the right objects when the list is truncated.
  `truncated_unordered_list` now decides whether an item is visible before it
  counts the item's children, as Django's own filter does. Before, a visible parent
  disappeared when its children crossed the limit and the "…and N more objects"
  count was wrong. It also no longer renders an empty `<ul>` when all the children
  are hidden.
- Schemas now load from a directory whose name contains percent escapes, such as
  `pct%20dir` or `a%41b`. The XSD compiler percent-decoded plain path sources, so
  a project, or an installation of the package, under such a directory failed with
  `dj_hyperview.E012` or `schema_invalid` even with default settings. The package
  now hands the compiler absolute file URIs.
- A malformed `schemaLocation` in an `EXTRA_SCHEMAS` file, such as
  `//[::1/x.xsd`, is now reported as `forbidden_schema_reference` (`dj_hyperview.E012`
  with `reason: forbidden_schema_reference` during configuration checks). Before,
  `urlparse` raised a bare `ValueError` that crashed `manage.py check`.
- The dependency system checks (`dj_hyperview.E013` and `dj_hyperview.E015`) no longer crash with `ValueError` when the module they look
  up is a stand-in in `sys.modules` without a spec, such as a `ModuleType`,
  `MagicMock` or `SimpleNamespace` that a test installs. `importlib` raises for such
  an entry, but it still imports, so the check now counts it as installed. The new `dj_hyperview.W007` follows the
  same rule.
- A `VALIDATION` limit that is an `int` subclass other than `bool` now fails the
  system check with `dj_hyperview.E009`, the rule `ValidationSettings` already
  enforced. Before, the check accepted it and the first read of the settings
  raised a bare `ValueError`.
- An XML declaration whose encoding name is not an XML 1.0 `EncName` (a letter
  followed by letters, digits, `.`, `_` or `-`), is longer than 64 characters, or
  names no codec, such as a name containing a NUL or a lone surrogate, now
  fails with `invalid_encoding`, like any other encoding that is not UTF-8. The
  codec lookup raises `ValueError` for such a name, not `LookupError`, so
  `validate_hxml`, `validate_fragment_hxml`, `validate_rendered_hxml`,
  `validate_template_source`, `HyperviewResponse` and the template loader leaked a
  bare `ValueError` or `UnicodeEncodeError` that no `TemplateValidationError`
  handler caught, and `publish_template`, model validation and the Admin form raised
  it instead of `ValidationError`. The name is now decided from the alias table of
  Python's `encodings` package, which is how the registry resolves it, and never
  passed to `codecs.lookup`. Python keeps every name it looks up, found or not, for
  the life of the process, so each distinct name in a declaration used to add an
  entry to a cache that is never emptied: 5,000 distinct names added 5,000 entries,
  20 names of 200 KB each kept 4.2 MB, and valid-looking names such as `zz0` to
  `zz4999` still added 5,000 after a grammar check alone. Every spelling of UTF-8
  that the registry accepts and the grammar allows is still accepted, for example
  `utf-8`, `UTF8`, `u8`, `utf` and `cp65001`; a spelling that only the lookup's own
  normalization accepted, such as `utf 8` or a trailing space, is rejected.
- A document is reported as `max_depth` only when libxml2 stops with a
  resource-limit error that mentions depth, or with its own "Excessive depth in
  document" message, which libxml2 before 2.13 reported as an internal error.
  Before, any syntax error whose message contained "depth" was reported as
  `max_depth`, so a mismatched end tag in an element named `depth`, or a duplicated
  attribute named `depth`, hid the real `malformed_xml` diagnosis.
- Checking a document for DTD and entity declarations is much faster after a
  declaration literal appears anywhere in it, even inside a comment or CDATA
  section. The scan used to test every character: about 1 MB took 215 ms rendered
  and 373 ms as template source, and now takes a few milliseconds. The result is
  unchanged.
- The `ADMIN.PERMISSION` system check no longer crashes with `NameError` on
  Python 3.14 when the permission callable annotates a parameter with a type that
  is imported only under `TYPE_CHECKING`.
- Importing the package no longer loads Django's test framework into production
  processes: `setting_changed` now comes from `django.core.signals`.
- A UTF-8 BOM followed by template whitespace before the XML declaration, such as
  a file that starts with a BOM and `{% load dj_hyperview %}`, now renders. The BOM
  is preserved and the whitespace after it is removed, as documented.
- `HyperviewEngine.get_template` raises `InvalidTemplateName` for an empty,
  `None` or non-string name on Django 5.2 and 6.1 alike. Django 6.1 rejects these
  before any loader runs, which surfaced as `TemplateNotFound` and an HTTP 500
  instead of the 400 that unsafe names produce.
- On Django 6.1, an `Accept` header with a media range longer than Django's 10,000
  character limit no longer makes `HyperviewMiddleware` fail the request. It is
  treated as not accepting Hyperview. Django 5.2 has no such limit and parses the
  header as before.

### Documentation

- The configuration guide no longer says that project schemas can import the
  Hyperview namespace. Only `xs:any` with that namespace lets a custom element hold
  Hyperview elements: an `xs:import` of it cannot supply Hyperview components,
  because every extra schema is also compiled on its own, without the Hyperview
  schema, and a `ref` or `type` that names one fails with `dj_hyperview.E012`
  (`reason: schema_invalid`).
- `VALIDATION.MAX_NODES` and `MAX_DEPTH` are documented as bounds on nodes, not
  only elements. Comments and processing instructions inside the root element have
  always counted toward both, which is the safer bound. Those outside the root
  element are not walked, so only `MAX_BYTES` bounds them.
- A system check reference in the configuration guide lists every `dj_hyperview.E`
  and `W` ID with its meaning and fix, and explains that `DummyCache` raises only
  `W004` yet makes every lookup fail under `FAILURE_MODE="raise"`.
- The HTTP guide documents `HyperviewMiddleware` and `request.hyperview`, and
  states that the middleware adds no `Vary` header: a view that returns HXML to
  Hyperview clients and something else to other callers on one URL must add
  `Vary: Accept, X-Hyperview-Version` itself. The README no longer calls request
  detection "content negotiation".
- The configuration guide documents the Redis 7 ACL rule that realtime needs
  (`resetchannels` plus the namespace's channels) and the limits of the `rediss://`
  and `unix://` options, and shows `publish_after_commit` with its real signature:
  `using` is keyword-only and required.
- Hyperview compatibility is a page of its own again, `hyperview-compatibility.md`,
  that describes the current 0.111.0 contract and the 0.110.0 history. It replaces
  the `hyperview-0.110.0` stub, whose URL no longer exists.
- The documentation no longer says that redis-py 7.4.1 is exercised against a live
  Redis: CI runs 8.1.0, and 7.4.1 is only the declared lower bound of the
  `[realtime]` extra. It also drops the removed `VALIDATION.MODE` from the database
  guide, the "confirm publication" hedging, the unsupported claim about Hyperview
  0.112.0, and HyperTodo instructions that no longer match that application.

### Compatibility

- Hyperview 0.112.0 removes `createTestProps` and changes native test-ID behavior
  in the client. The server contract is unchanged: 0.1.0b2 ships and validates
  against the corrected 0.111.0 schema, and this repository records upstream
  provenance for 0.110.0 and 0.111.0 only. This note does not publish a new beta.

### Maintenance

- Refresh compatible documentation/development dependencies, the pinned CI uv
  patch, and verified setup-node/setup-uv action releases without changing the
  published package's version or runtime dependency bounds.
- Pin the CI uv patch to 0.12.22, the release that regenerated `uv.lock` (lock
  format revision 5), so `uv sync --locked` in CI uses the uv that wrote the lock.
- Give every workflow a `concurrency` group, in which only a pull request cancels
  its earlier run, and make every checkout drop its token from the git
  configuration (`persist-credentials: false`). The CI group has its own prefix,
  because a workflow that the release calls sees the release's name in
  `github.workflow`, and the same group in both deadlocks the run. The default
  token was already read-only, with write access granted per job.
- Declare PyYAML, which the tests import, in the `dev` dependency group, make the
  release tag tool read the `pyproject.toml` beside it instead of the current
  directory's, and ignore the `site/` documentation build.
- Stop shipping package data that nothing read at runtime: the unreferenced
  `schemas/0.111.0/catalog.json`, and the package-owned `r2` corrections, margin
  adaptation and committed catalog of the archived 0.110.0 schemas. The upstream
  0.110.0 XSDs stay, so `get_hyperview_schema_path()` and `build_hyperview_catalog()`
  are unchanged, and the package carries about 410 KB less data. The tests that only
  compared those files with each other are removed with them.
- Remove test and tooling code that pinned nothing: the `--show-matrix` option and
  `matrix_commands()` of `tools.test_matrix`, which CI and the documentation never
  used; a duplicate test of the coverage runner; seven of eight editor theme tests,
  which asserted one `setTheme` call after the theme listeners they exercised were
  removed; an unused XML fixture; and the assertions that the old package names
  `django_hv` and `django-hyperview` do not appear.

## 0.1.0b2 — 2026-09-24

### Added

- Validate against Hyperview 0.111.0, including optional
  boolean `content-insets` on scroll-capable elements. The 0.110.0 upstream
  resources and inspection helper remain available unchanged; the new corrected
  registry and its editor catalog share one 0.111.0 contract.

### Fixed

- The optional Admin HXML editor stays dark from initialization, regardless of the Admin theme, theme toggles, or system color preference.

### Changed

- Admin **Format and Validate** reports expected dynamic-template limits as a
  short, neutral informational note (`schema_static_incomplete`, severity `info`)
  rather than a warning. Source navigation, blocking errors, save checks, and
  mandatory validation of rendered HXML are unchanged.

### Documentation

- Explain the Django-to-Hyperview request lifecycle in the Quick Start, with an
  executable full-screen and tap-to-fragment example, server/client responsibilities
  and direct links from the landing, mobile and HTTP response guides.

### Compatibility

- The HyperTodo test application's mobile client now pins upstream Hyperview 0.111.0,
  whose [#1348 fix](https://github.com/Instawork/hyperview/pull/1350)
  distinguishes failed fragment fetches from intentional sync drops. Mounted client
  tests in that application cover HTTP 500, network rejection, indicator and
  pull-refresh cleanup, successful retry, and a dropped request. This is not a
  package change: no server SSE protocol change or React Navigation migration is
  required.

## 0.1.0b1 — 2026-09-10

### Changed

- Start the 0.1 beta line with `Development Status :: 4 - Beta` metadata and an
  explicit within-line public-API compatibility commitment. Incompatible changes
  require migration guidance, changelog disclosure and regression tests;
  security fixes may require an accelerated, documented exception.
- Formalize the tested contract already shipped in a22. This release introduces
  no new package SSE runtime behavior; HyperTodo mobile fixes and its platform
  acceptance belong to that application's own release and tests.

### Documentation

- Consolidate superseded local plans into one current roadmap, preserve pending
  upstream and capability work, and keep the old Hyperview compatibility URL as
  a link to the canonical testing section.
- State the exact Python/Django, Admin, SQLite, Redis and HXML test scope and
  deployment limits. Beta does not imply PostgreSQL/MySQL, load/soak or universal
  native-platform proof; SSE remains best-effort with no durable replay guarantee.

## 0.1.0a22 — 2026-09-10

### Added

- Realtime invalidate v2 adds bounded opaque mutation/entity metadata
  and the public `INVALIDATION_VERSIONS` capability while preserving exact v1
  envelopes and after-commit capture. Applications must negotiate/project for
  old clients and deploy backend workers together. Updated guidance separates
  package primitives, coordinated application behavior and exact v1 fallback;
  package tests alone are not native acceptance.

### Fixed

- Normal CI now includes the live Redis profile while preserving all six
  no-Redis compatibility cells and explicit base-only callers. The coverage
  collector verifies current-run artifact identities, exact counters and
  report digests before uploading both actual XML reports together; previously
  the executed Redis coverage was absent from the upload. External Codecov
  acceptance remains distinct from successful upload.
- Release now requires an exact-SHA successful main CI acceptance receipt plus
  current successful Codecov project/patch statuses before staging. Ordinary CI
  explicitly assesses merged reports; identical evidence can reuse or promote
  an immutable assessment without duplicate uploads. Bounded waits occur outside
  the SHA lock for producers, and missing, expired or changed proof fails closed.
  Coverage thresholds and Codecov automatic targets are unchanged.

## 0.1.0a21 — 2026-09-09

### Added

- Centralized optional `HYPERVIEW.REALTIME` configuration with exact `REDIS_URL`
  and `NAMESPACE`, frozen `get_settings().realtime`, sanitized `E022` checks and
  no Redis import/network during settings access. Omitted/None disables it;
  cache-alias adaptation is not implemented. The configuration guide now opens
  with a complete executable example, then explains each section in plain English.
- Step-by-step SSE guide with verified ASGI/publication snippets, typed
  list/form examples and an explicit package-versus-application integration boundary.
- Optional `[realtime]` Redis broker with immutable after-commit capture, actual
  subscription ACK admission, bounded queues and conservative resync hints.
- Public `realtime_asgi` request ownership and `sse_response` closed framing;
  cleanup survives sync middleware adaptation and pre-frame disconnects.
  These are transport primitives, not application authorization or replay.

- Public frozen `TemplateInvalidation` events and robust `template_invalidated`
  signal for supported database template mutations after commit. Notifications
  share the cache callback, including disabled/failing cache paths, without
  suppressing existing cache exceptions or requiring Redis/network transport.

### Fixed

- Fresh-process realtime tests now explicitly locate the repository source,
  independent of an inherited `PYTHONPATH`. Redis-import and network prohibitions
  remain enforced; no runtime transport behavior changed.

## 0.1.0a20 — 2026-09-09 (not published)

- Release checks failed before artifact staging and publication because three
  fresh-process tests could not import the source package in CI. The tag is
  retained unchanged; the corrected SSE release is a21 above.

## 0.1.0a19 — 2026-09-09

### Fixed

- Context-free Admin validation now ignores XML comments, CDATA sections, and
  processing instructions while locating Django tokens. Literal
  `{% include %}` tags that follow documentation containing quotes are expanded
  correctly instead of producing false text-content schema errors.

## 0.1.0a18 — 2026-09-09

### Added

- Context-free Admin validation uses Django's parser for actionable syntax
  diagnostics and recursively resolves every literal `{% include %}` through
  the configured Hyperview source precedence. Included source is checked both
  independently and in its parent position; missing, unsafe, unavailable,
  cyclic, and excessive expansion is rejected without exposing private source
  details.
- Statically visible HXML child placement and order are validated against the
  same corrected schema used for rendered responses. Django expressions in
  text or quoted attributes are treated as an unknown scalar, preserving
  structural validation without inventing runtime values.

### Changed

- The Admin HXML editor uses a 15-pixel font and two-space soft tabs.
- Runtime schema failures now explain unexpected children, incomplete content,
  forbidden or missing attributes, and invalid typed values without including
  rendered data or internal paths.

### Fixed

- Mutually exclusive Django branches no longer create false child-order errors.
  Conditional structure, inheritance, and variable-selected includes instead
  produce an explicit incomplete-analysis warning and remain subject to final
  rendered-response validation.

## 0.1.0a17 — 2026-09-08

### Fixed

- Context-free Admin validation no longer treats non-rendering `{% load %}` tags
  or inline Django comments as incomplete dynamic HXML. When later runtime
  values remain, the warning points to the first value that actually requires
  rendering instead of blaming the template preamble.

## 0.1.0a16 — 2026-09-08

### Added

- One automatically enforced Hyperview 0.110.0 schema includes narrowly verified
  text, image, accessibility, date-field, and percentage-margin corrections.
  Only `style@width` is constrained to its supported numeric forms. Schema
  revision tracking is internal; upstream resources remain unchanged.
- Typed `SCHEMA_EXTENSIONS` declarations describe application-owned behavior
  attributes and extra attributes on specific built-in elements without opening
  standard actions or allowing arbitrary attributes. Rendered validation,
  context-free Admin checks, and editor completion share this single contract.
  See [custom schema declarations](custom-schemas.md).
- `HYPERVIEW_VALIDATION_CONTRACT="automatic-xsd-v1"` identifies automatic
  validation support for consumers that must reject an incompatible package.
  This exported capability is not a setting or a package release version.

### Breaking changes

- Remove `SCHEMA_PROFILE`, `VALIDATION.MODE`, and `VALIDATION.SCHEMA` from
  settings. Their presence now raises a configuration error with migration
  guidance. `ValidationSettings` accepts safety limits only; validation cannot
  be disabled or replaced by a callback.
- `xmlschema` is a normal dependency. The old `[schema]` installation extra is
  retained as an empty compatibility alias; `[editor]` remains optional.
- Hyperview responses require complete, valid HXML. Body assignments, `write()`,
  and `writelines()` validate atomically; failed updates preserve the prior body.
  Empty document bodies are allowed only for 204, 205, and 304, which reject
  nonempty bodies. HEAD validates the document before suppressing its transport
  body; pass `request=request` to eager responses for this request context.
  Assemble incremental XML before assigning it.
- Django gzip transport is accepted only when it encodes the already-validated
  document exactly. Compressed input cannot introduce a new document or bypass
  validation. Remove `Content-Encoding` before replacing the document afterward.
- Adopt package and application configuration together after reviewing existing
  database overrides. Previously unchecked invalid output now fails validation;
  do not use seed commands, automatic repairs, or shared-cache flushes to migrate.

### Fixed

- The active completion catalog preserves attribute namespaces and offers custom
  behavior attributes only for the selected literal action. Namespaced standard
  attributes no longer collide with unqualified application attributes in that
  catalog; upstream inspection helpers retain their original output.
- Incompatible duplicate declarations are rejected consistently, including
  differences inherited from schema defaults and namespace bindings, rather than
  letting the order of external schemas change validation results.
- External elements with simple XSD types no longer crash completion or Admin
  validation catalogs. They expose no custom attributes; validation stays strict.

### Changed

- The optional Admin editor now runs **Format and Validate** before **Save**,
  **Save and add another**, and **Save and continue editing**. Errors block the
  write with inline diagnostics, warning-only results continue the selected
  action, and the server form repeats validation when JavaScript is bypassed.

## 0.1.0a15 — 2026-09-08

### Changed

- The optional Admin editor now exposes one **Format and Validate** action. It
  applies conservative formatting first and then validates the resulting Ace
  buffer. When formatting is unsafe, validation still runs against the unchanged
  source and reports both outcomes independently.

## 0.1.0a14 — 2026-09-08

### Added

- **Validate source** now checks statically visible HXML elements, attributes,
  required attributes, and literal enumerations against the selected XSD catalog,
  including project `EXTRA_SCHEMAS`. Dynamic values remain deferred to full
  rendered-response validation, and incomplete static analysis is reported as a
  warning rather than a false success.

## 0.1.0a13 — 2026-09-08

### Added

- **Validate source** in the optional Admin editor checks an unsaved template name
  and source without context, rendering, persistence, or revision changes. It
  reports safe source diagnostics and Django syntax line numbers when available.

### Removed

- The experimental static Admin template preview and its server-configured
  scenarios. Template authoring keeps the Ace editor, formatter, schema-backed
  completion, and publication validation.

## 0.1.0a12 — 2026-09-08

### Added

- Opt-in Admin template preview with configured example-data scenarios, unsaved
  draft rendering, structured diagnostics, and an isolated static web view.
  Preview is disabled by default, keeps the editor buffer unchanged, and never
  publishes a template.

### Fixed

- Static preview now applies styles declared at document level and refreshes
  visual warning state when selecting another screen.

## 0.1.0a11 — 2026-09-08

### Upgrade notes

- Django `FileBasedCache` and its subclasses are rejected because their
  non-atomic `add()` cannot protect invalidation barriers. Configure a compatible
  backend or disable Hyperview caching; `bypass` does not suppress this error.
  See [cache migration guidance](cache-consistency.md).
- `bulk_create(update_conflicts=True)` now raises `NotSupportedError` before
  consuming input or writing. Normal inserts and `ignore_conflicts` remain
  supported; use [publication services](database-admin.md) for updates.
- Run the read-only `check_hyperview_templates --database ALIAS` command only on
  authorized databases before deployment. Invalid names, incorrect identities,
  or duplicates require a backed-up, human-reviewed repair, not an automatic
  migration. See [historical integrity checks](database-admin.md#check-historical-template-integrity).

### Added

- Opt-in `SCHEMA_PROFILE="compatible-0.110.0"` permits percentages in the nine
  style margin attributes without broadening unrelated types. The default
  `upstream-0.110.0` and standalone upstream helpers retain their existing
  meaning. Validation and autocomplete use the selected profile together. See
  [custom schemas](custom-schemas.md).
- The package README and documentation landing page now link to
  [HyperTodo](https://github.com/eamigo86/HyperTodo), the maintained Django and
  Expo test application.

### Fixed

- Declaration scanning is linear, including unclosed Django delimiters; only
  raw template sources interpret Django comments. Rendered XML retains its
  independent safety checks and escaping. See [XML security](security.md).
- An initial BOM no longer conceals incompatible XML encoding declarations;
  valid UTF-8 BOM content remains unchanged.
- Filesystem permission and I/O failures remain `SourceUnavailable` on Python
  3.14 instead of selecting a lower-priority template. See [filesystem sources](filesystem.md).
- Read-only Admin users can view templates while the Ace editor is enabled.
- HXML formatting preserves meaningful whitespace, mixed content, CDATA,
  preformatted text and Django syntax, including `blocktranslate` and
  `blocktrans` bodies; ambiguous input remains unchanged. See [Admin
  authoring](database-admin.md).
- Nested deletions use both database alias and primary key when tracking
  invalidations; commit and rollback keep their existing guarantees.
- Generator-based `update_fields` is consumed once without dropping fields.
- Extra XSDs reject `xs:override`; only the fixed bundled compatibility
  adaptation uses it. Validation and completion both inspect transitive local
  references before reusing dependency-aware caches. See [schema safety](security.md).
- The compatibility runner enforces **lines ≥95% and branches ≥95%** separately
  after combining base and Admin coverage, while retaining the combined gate.
  Missing, malformed and stale reports cannot pass. See [testing](testing.md).

## 0.1.0a10 — 2026-09-07

### Added

- `ADMIN.PERMISSION` as an authoritative callable or dotted-path policy for
  stored-template creation, changes, and deletion, with a superuser-only default.
- Detailed custom-schema guidance for project-owned namespaced HXML elements,
  validation, and editor autocomplete.

### Changed

- The HXML Ace editor now fills the available Django 5.2 and Django 6.1 admin
  form width instead of collapsing to its gutter inside the admin flex layout.

### Security

- Template mutation callbacks grant access only when they return the literal
  boolean `True`; import problems fail system checks and runtime failures deny
  access.

## 0.1.0a9 — 2026-09-07

### Added

- A versioned Hyperview 0.110.0 XSD registry, provenance, checksums, and a
  deterministic completion catalog.
- Optional `[schema]` support for XSD 1.1 validation of rendered documents and
  fragments, including project-owned custom schemas.
- An optional `[editor]` profile with a CSP-safe HXML Ace editor, contextual
  completion, Django-template highlighting, safe formatting, and theme-aware
  presentation.
- Safe line and column coordinates on `TemplateValidationError` when available.

### Changed

- Database publication now compiles Django template syntax before any write or
  revision increment, without rendering context-dependent output.
- Schema and catalog caches now follow settings and local-file fingerprints.
- CI now tests editor JavaScript on Node 24 and smoke-tests base, schema, and
  editor wheel profiles.

### Security

- Custom schemas reject remote references, local-root escapes, and incompatible
  duplicate declarations.
- The admin catalog endpoint requires authentication and model view permission;
  editor assets are local and avoid inline scripts.

## 0.1.0a1 to 0.1.0a7 — 2026-09-04 to 2026-09-05

These early alphas predate this changelog, so no per-release notes exist. The Git
tags `v0.1.0a1` to `v0.1.0a7` and the history behind them are the record.

## 0.1.0a8 — 2026-09-06

### Added

- First-class eager and lazy fragment responses with the
  `application/vnd.hyperview_fragment+xml` media type.
- A PEP 561 `py.typed` marker and the public `dj_hyperview.__version__` value.
- A standalone Expo and Hyperview mobile Getting Started guide.

### Changed

- Hardened database publication, byte-exact identity handling, legacy-row
  recovery, mutation routing, and transaction-aware invalidation.
- Isolated the package template engine from unsafe consumer engine options.
- Preserved cache-safe source results across mixed source chains.
- Reported absent source configuration and missing filesystem roots as
  actionable Django system-check warnings.
- Reused validated settings and the default rendering engine until a dependent
  Django setting changes.
- Rejected unsafe `bulk_create()` names and invalidated cache state after bulk
  writes, including conflict updates.

### Compatibility

- Tested against Django 5.2 and Django 6.1 on supported Python versions.
- Fragment update endpoints must return a bare client-safe element. Full HXML
  documents continue to use `application/vnd.hyperview+xml`.
