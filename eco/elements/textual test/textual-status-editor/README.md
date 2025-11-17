# textual-status-editor

This project implements a textual user interface for displaying and managing the status of various items in an assembly. It provides a tabular view of current values and allows users to set target values for these items.

## Features

- Display current values of status items in a tabular format.
- Entry fields for setting target values of status items.
- Interactive user interface built with the Textual library.

## Project Structure

```
textual-status-editor
├── src
│   └── textual_status_editor
│       ├── __init__.py
│       ├── app.py
│       ├── main.py
│       ├── config.py
│       ├── adapters
│       │   └── assembly_adapter.py
│       ├── models
│       │   └── status_item.py
│       └── widgets
│           ├── __init__.py
│           └── status_table.py
├── tests
│   ├── test_status_table.py
│   └── test_assembly_adapter.py
├── pyproject.toml
├── requirements.txt
├── README.md
├── .gitignore
└── LICENSE
```

## Installation

1. Clone the repository:
   ```
   git clone https://github.com/yourusername/textual-status-editor.git
   cd textual-status-editor
   ```

2. Install the required dependencies:
   ```
   pip install -r requirements.txt
   ```

## Usage

To run the application, execute the following command:
```
python -m textual_status_editor.main
```

## Contributing

Contributions are welcome! Please feel free to submit a pull request or open an issue for any suggestions or improvements.

## License

This project is licensed under the MIT License. See the LICENSE file for more details.