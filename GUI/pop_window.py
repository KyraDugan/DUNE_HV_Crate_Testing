import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk

def show_image_popup(
    title="Image Viewer",
    image_path=None,
    button_text="Confirm"
):
    """
    Display a fullscreen popup showing an image.
    Press ESC to exit fullscreen or click the button (button_text) to close it.
    """

    def exit_fullscreen(event=None):
        root.attributes('-fullscreen', False)

    def close_window():
        root.destroy()

    # === Initialize window ===
    root = tk.Tk()
    root.title(title)
    root.attributes('-fullscreen', True)
    root.bind("<Escape>", exit_fullscreen)

    # === Screen size ===
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()

    # === Main frame ===
    main_frame = ttk.Frame(root, padding=20)
    main_frame.pack(fill="both", expand=True)

    # === Image display ===
    if image_path:
        try:
            img = Image.open(image_path)

            # Fit image to screen (keeping aspect ratio)
            img_ratio = img.height / img.width
            target_width = screen_width - 100
            target_height = int(target_width * img_ratio)

            # If image too tall, scale by height instead
            if target_height > screen_height - 150:
                target_height = screen_height - 150
                target_width = int(target_height / img_ratio)

            img = img.resize((target_width, target_height), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(img)

            image_label = ttk.Label(main_frame, image=photo)
            image_label.image = photo
            image_label.pack(expand=True)
        except Exception as e:
            print(f"Error loading image: {e}")
            ttk.Label(main_frame, text="Error loading image.", font=("Arial", 24)).pack(expand=True)

    # === Bottom Close Button ===
    ttk.Button(
        main_frame,
        text=button_text,
        command=close_window
    ).pack(padx=40,pady=40)

    root.mainloop()

def show_checkbox_popup(
    options,
    title="Select Options",
    image_path=None
):
    selected_options = []

    def toggle_all():
        state = select_all_var.get()
        for var in checkbox_vars:
            var.set(state)

    def on_submit():
        nonlocal selected_options
        selected_options = [
            options[i] for i, var in enumerate(checkbox_vars) if var.get()
        ]
        root.destroy()

    def exit_fullscreen(event=None):
        root.attributes('-fullscreen', False)

    # === Initialize window ===
    root = tk.Tk()
    root.title(title)
    root.attributes('-fullscreen', True)
    root.bind("<Escape>", exit_fullscreen)

    # === Font ===
    font_style = ("Arial", 24)

    # === Screen size ===
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()

    # === Main frame ===
    main_frame = ttk.Frame(root, padding=20)
    main_frame.pack(fill="both", expand=True)

    # Grid configuration: left (fixed), right (stretch)
    main_frame.columnconfigure(0, weight=0)
    main_frame.columnconfigure(1, weight=1)
    main_frame.rowconfigure(0, weight=1)

    # === LEFT COLUMN: Fixed width for 60 characters ===
    char_width_px = 12  # estimate for 24pt font
    target_width = 40 * char_width_px  # ~840 px

    checkbox_frame = ttk.Frame(main_frame, width=target_width)
    checkbox_frame.grid(row=0, column=0, sticky="nsw", padx=(0, 20))
    checkbox_frame.grid_rowconfigure(99, weight=1)
    checkbox_frame.grid_propagate(False)

    # "Select All"
    select_all_var = tk.BooleanVar()
    tk.Checkbutton(
        checkbox_frame,
        text="{}".format(title),
        variable=select_all_var,
        command=toggle_all,
        font=font_style
    ).grid(row=0, column=0, sticky="w", pady=(0, 10))

    # Checkboxes with auto-wrapping labels
    checkbox_vars = []
    for i, opt in enumerate(options, start=1):
        var = tk.BooleanVar()
        tk.Checkbutton(
            checkbox_frame,
            text=opt,
            variable=var,
            font=font_style,
            wraplength=target_width - 40,  # wrap to fit inside column
            justify="left"
        ).grid(row=i, column=0, sticky="w", pady=4)
        checkbox_vars.append(var)

    # Submit button at bottom-left
    tk.Button(
        checkbox_frame,
        text="Submit",
        command=on_submit,
        font=font_style
    ).grid(row=99, column=0, sticky="sw", pady=(20, 0))

    # === RIGHT COLUMN: Image ===
    if image_path:
        try:
            img = Image.open(image_path)

            # Set width to 2/3 of screen, minus padding
            img_width = int((screen_width * 2 / 3) - 100)
            img_ratio = img.height / img.width
            img_height = int(img_width * img_ratio)

            # Clamp if too tall
            if img_height > screen_height - 100:
                img_height = screen_height - 100
                img_width = int(img_height / img_ratio)

            img = img.resize((img_width, img_height), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(img)

            image_label = ttk.Label(main_frame, image=photo)
            image_label.image = photo
            image_label.grid(row=0, column=1, sticky="nsew", padx=40)
        except Exception as e:
            print(f"Error loading image: {e}")

    root.mainloop()
    return selected_options

def show_input_popup(
    title="Input",
    image_path=None,
    prompt="",
    fields=(),
    buttons=("Submit",),
    message="",
    heading="",
    one_line=False
):
    """
    Fullscreen slide with text boxes and answer buttons under it.

    fields   labels of the text boxes, one box per label (may be empty)
    buttons  labels of the answer buttons; clicking one closes the window
    message  red line above the boxes, e.g. why the last answer was not accepted
    heading  bold line between the slide and the prompt, e.g. the WIB slot an error is about
    one_line all the boxes on one line, each short box after its label (e.g. DUNE- [ ] REV [ ] SN [ ])

    Returns {"button": label clicked, "fields": [text of each box]}, or None if the
    window was closed without an answer.
    """
    result = None

    def choose(label):
        nonlocal result
        result = {"button": label, "fields": [var.get().strip() for var in field_vars]}
        root.destroy()

    def exit_fullscreen(event=None):
        root.attributes('-fullscreen', False)

    # === Initialize window ===
    root = tk.Tk()
    root.title(title)
    root.attributes('-fullscreen', True)
    root.bind("<Escape>", exit_fullscreen)

    font_style = ("Arial", 24)

    # === Window size ===
    # The fullscreen window covers one monitor. winfo_screenwidth() would give both monitors
    # together, so read the window itself once it is open
    root.update()
    screen_width = root.winfo_width() if root.winfo_width() > 1 else root.winfo_screenwidth()
    screen_height = root.winfo_height() if root.winfo_height() > 1 else root.winfo_screenheight()
    wrap_width = screen_width - 200     # longer lines wrap before the edge of the screen

    # === Main frame ===
    main_frame = ttk.Frame(root, padding=20)
    main_frame.pack(fill="both", expand=True)

    # === Heading, prompt and message ===
    # Made before the image but packed after it, so the image can leave room for every line they wrap onto
    texts = []
    if heading:
        texts.append((tk.Label(main_frame, text=heading, font=font_style + ("bold",), wraplength=wrap_width), (10, 0)))
    if prompt:
        texts.append((tk.Label(main_frame, text=prompt, font=font_style, wraplength=wrap_width), (10, 0)))
    if message:
        texts.append((tk.Label(main_frame, text=message, font=font_style, fg="red", wraplength=wrap_width), 0))
    root.update_idletasks()
    text_height = sum(label.winfo_reqheight() + 10 for label, _ in texts)

    # === Image display ===
    # Same fit-to-screen as show_image_popup, but leaves room under it for the text above,
    # one row per text box (one in all for one_line) and the buttons
    box_rows = min(len(fields), 1) if one_line else len(fields)
    reserved = 200 + 70 * box_rows + text_height
    if image_path:
        try:
            img = Image.open(image_path)

            img_ratio = img.height / img.width
            target_width = screen_width - 100
            target_height = int(target_width * img_ratio)

            if target_height > screen_height - reserved:
                target_height = screen_height - reserved
                target_width = int(target_height / img_ratio)

            img = img.resize((target_width, target_height), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(img)

            image_label = ttk.Label(main_frame, image=photo)
            image_label.image = photo
            image_label.pack(expand=True)
        except Exception as e:
            print(f"Error loading image: {e}")
            ttk.Label(main_frame, text="Error loading image.", font=font_style).pack(expand=True)

    for label, pady in texts:
        label.pack(pady=pady)

    # === Text boxes: label on the left, box on the right; one row each, or all in one row ===
    field_vars = []
    entries = []
    if one_line:
        row = ttk.Frame(main_frame)
        row.pack(pady=5)
    for label in fields:
        if not one_line:
            row = ttk.Frame(main_frame)
            row.pack(pady=5)
            tk.Label(row, text=label, font=font_style, width=28, anchor="e").pack(side="left", padx=10)
        else:
            tk.Label(row, text=label, font=font_style).pack(side="left", padx=(20, 5))
        var = tk.StringVar()
        entry = tk.Entry(row, textvariable=var, font=font_style, width=8 if one_line else 30)
        entry.pack(side="left")
        field_vars.append(var)
        entries.append(entry)

    # === Answer buttons, side by side ===
    button_row = ttk.Frame(main_frame)
    button_row.pack(pady=20)
    for label in buttons:
        # label=label: each button keeps its own label (a plain lambda would see the last one)
        tk.Button(button_row, text=label, font=font_style,
                  command=lambda label=label: choose(label)).pack(side="left", padx=30)

    # Enter only answers when there is a single button; with PASS/FAIL it must be a click
    if len(buttons) == 1:
        root.bind("<Return>", lambda event: choose(buttons[0]))

    # A fullscreen window does not always get the keyboard, so put the cursor in the first box
    if entries:
        root.after(200, entries[0].focus_force)

    root.mainloop()
    return result
