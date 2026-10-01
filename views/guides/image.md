## Building an image view

An `image` view shows one picture: `spec.src` is a view asset or an https URL, `spec.caption` says what it shows.

- Write or copy the file into the workspace, then `create_view` with kind `image`, spec `{"src": "chart.png", "caption": "..."}` and `files: ["chart.png"]`; the file becomes a view asset. `view_add_asset` binds another file later.
- Always give a caption that would make sense without the picture.
