# Aoi Launcher

A modern Windows launcher with AI integration, global hotkeys, and smart search capabilities.

## ✨ Features

- **🔍 Smart Search** - Fast file and application search
- **🤖 AI Integration** - Support for OpenAI, Anthropic, Gemini, and local Ollama
- **⌨️ Global Hotkeys** - Customizable keyboard shortcuts (default: Ctrl+Space)
- **🧮 Calculator** - Built-in mathematical calculations
- **🌐 Web Search** - Quick access to Google, YouTube, GitHub, and more
- **⚡ System Commands** - Launch system tools and utilities
- **🎨 Modern UI** - Clean, dark theme with smooth animations

## 🚀 Installation

### Prerequisites
- Windows 10/11
- Python 3.8+
- PyQt6

### Quick Start
1. Clone the repository:
```bash
git clone https://github.com/yourusername/aoi-launcher.git
cd aoi-launcher
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Run the launcher:
```bash
python AOI.py
```

## 📋 Requirements

Create a `requirements.txt` file with:
```
PyQt6
pywin32
requests
```

## 🎯 Usage

### Basic Search
- Press `Ctrl+Space` to open the launcher
- Type to search for files, applications, or commands
- Press `Enter` to launch selected item
- Press `Escape` to close

### AI Commands
- Use `ai:` prefix for AI queries: `ai: what is machine learning?`
- Configure AI services in settings
- Switch between different AI providers

### Special Commands
- **Math**: `2+2`, `15% of 200`
- **Web**: `google python`, `youtube music`
- **System**: `calculator`, `notepad`, `shutdown`
- **Text**: `encode base64 hello`, `generate password`

## ⚙️ Configuration

Access settings by typing `options` in the launcher:
- **General**: Search delay, window behavior
- **AI & APIs**: Service selection, API keys
- **Appearance**: Themes, fonts, animations
- **Shortcuts**: Global hotkey configuration
- **Performance**: Cache settings, debug mode

## 🔧 AI Services Setup

### OpenAI
1. Get API key from [OpenAI](https://platform.openai.com/)
2. Add to settings: `ai config openai api_key=your_key_here`

### Anthropic
1. Get API key from [Anthropic](https://console.anthropic.com/)
2. Add to settings: `ai config anthropic api_key=your_key_here`

### Gemini
1. Get API key from [Google AI Studio](https://makersuite.google.com/)
2. Add to settings: `ai config gemini api_key=your_key_here`

### Ollama (Local)
1. Install [Ollama](https://ollama.ai/)
2. Run `ollama run llama2`
3. Use `ai switch ollama` to switch service

## 🎨 Customization

- Modify themes in the appearance settings
- Adjust search delay and result limits
- Customize global hotkey combinations
- Enable/disable animations and effects

## 🐛 Troubleshooting

### Common Issues
- **Global hotkey not working**: Check if another app uses the same shortcut
- **AI not responding**: Verify API keys and service status
- **Slow search**: Adjust search delay in settings

### Debug Mode
Enable debug mode in settings to see detailed logs and troubleshoot issues.

## 📝 License

This project is open source. Feel free to contribute and improve!

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## 📞 Support

If you encounter issues or have questions:
- Open an issue on GitHub
- Check the troubleshooting section
- Review the debug logs

---

**Enjoy using Aoi Launcher! 🚀**
