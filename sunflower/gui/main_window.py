from __future__ import absolute_import
from builtins import map, filter

import os
import sys
import webbrowser
import shlex
import subprocess
import signal
import fcntl

from gi.repository import Gtk, Gdk, GLib, GObject, Pango, Gio
from importlib import import_module
from pathlib import Path
from functools import partial
from operator import contains

from sunflower import common
from sunflower.menus import MenuManager
from sunflower.mounts import MountsManager
from sunflower.icons import IconManager
from sunflower.emblems import EmblemManager
from sunflower.associations import AssociationManager
from sunflower.indicator import Indicator
from sunflower.notifications import NotificationManager
from sunflower.toolbar import ToolbarManager
from sunflower.accelerator_group import AcceleratorGroup
from sunflower.accelerator_manager import AcceleratorManager
from sunflower.keyring import KeyringManager, InvalidKeyringError
from sunflower.parameters import Parameters

from sunflower.plugin_base.item_list import ItemList
from sunflower.plugin_base.rename_extension import RenameExtension
from sunflower.plugin_base.find_extension import FindExtension
from sunflower.plugin_base.terminal import TerminalType
from sunflower.widgets.location_menu import LocationMenu
from sunflower.tools.advanced_rename import AdvancedRename
from sunflower.tools.find_files import FindFiles
from sunflower.tools.version_check import VersionCheck
from sunflower.tools.disk_usage import DiskUsage
from sunflower.config import Config

# user interface imports
from sunflower.gui.about_window import AboutWindow
from sunflower.gui.preferences_window import PreferencesWindow
from sunflower.gui.preferences.display import TabExpand
from sunflower.gui.input_dialog import InputDialog, AddBookmarkDialog
from sunflower.gui.keyring_manager_window import KeyringManagerWindow


class MainWindow(Gtk.ApplicationWindow):
	"""Main application class"""

	# in order to ease version comparing build number will
	# continue increasing and will never be reset.
	version = {
			'major': 0,
			'minor': 4,
			'build': 62,
			'stage': 'f'
		}

	NAUTILUS_SEND_TO_INSTALLED = common.executable_exists('nautilus-sendto')

	def __init__(self, application, dont_load_plugins):
		# create main window and other widgets
		Gtk.ApplicationWindow.__init__(self, application=application)

		# set application name
		GLib.set_application_name('Sunflower')

		# local variables
		self._geometry = None
		self._active_object = None
		self._accel_group = None

		# load custom styles
		self._load_styles()

		# containers
		self.plugin_classes = {}
		self.provider_classes = {}
		self.archive_provider_classes = {}
		self.rename_extension_classes = {}
		self.find_extension_classes = {}
		self.mount_manager_extensions = []
		self.column_extension_classes = []
		self.column_editor_extensions = []
		self.popup_menu_actions = []
		self.viewer_extensions_classes = []

		# list of protected plugins
		self.protected_plugins = ('file_list', 'system_terminal')

		# create managers early
		self.icon_manager = IconManager(self)
		self.emblem_manager = EmblemManager(self)
		self.menu_manager = MenuManager(self)
		self.mount_manager = MountsManager(self)
		self.associations_manager = AssociationManager(self)
		self.notification_manager = NotificationManager(self)
		self.toolbar_manager = ToolbarManager(self)
		self.accelerator_manager = AcceleratorManager(self)
		self.keyring_manager = KeyringManager(self)

		# set window title
		self.set_title(_('Sunflower'))
		self.set_wmclass('Sunflower', 'Sunflower')

		# set window icon
		self.icon_manager.set_window_icon(self)

		# config parsers
		self.options = None
		self.window_options = None
		self.tab_options = None
		self.bookmark_options = None
		self.toolbar_options = None
		self.command_options = None
		self.accel_options = None
		self.association_options = None
		self.mount_options = None

		# config and plugin paths
		self.config_path = None
		self.system_plugin_path = None
		self.user_plugin_path = None

		# create a clipboard manager
		self.clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)

		# load config
		self.load_config()

		# connect delete event to main window
		if self.window_options.section('main').get('hide_on_close'):
			self.connect('delete-event', self._delete_event)
		else:
			self.connect('delete-event', self._destroy)

		signal.signal(signal.SIGTERM, self._destroy)
		signal.signal(signal.SIGINT, self._destroy)

		self.connect('configure-event', self._handle_configure_event)
		self.connect('window-state-event', self._handle_window_state_event)

		# create other interfaces
		self.indicator = Indicator(self)
		self.preferences_window = PreferencesWindow(self)
		self.disk_usage = DiskUsage(self)

		# create header bar
		self.header_bar = Gtk.HeaderBar.new()
		self.header_bar.set_has_subtitle(True)
		self.header_bar.set_show_close_button(True)
		self.header_bar.set_title(_('Sunflower'))
		self.set_titlebar(self.header_bar)

		# create bar buttons
		self.new_tab_actions = Gio.SimpleActionGroup.new()
		self.new_tab_menu = Gio.Menu()
		self.commands_actions = Gio.SimpleActionGroup.new()
		self.commands_menu = Gio.Menu()

		image_new = Gtk.Image.new_from_icon_name('tab-new-symbolic', Gtk.IconSize.BUTTON)
		self.button_new = Gtk.MenuButton.new()
		self.button_new.set_image(image_new)
		self.button_new.set_menu_model(self.new_tab_menu)
		self.button_new.insert_action_group('new-tab', self.new_tab_actions)

		self.button_commands = Gtk.MenuButton.new()
		self.button_commands.set_label(_('Commands'))
		self.button_commands.set_menu_model(self.commands_menu)
		self.button_commands.insert_action_group('commands', self.commands_actions)

		self.header_bar.pack_start(self.button_new)
		self.header_bar.pack_start(self.button_commands)

		# define local variables
		self._in_fullscreen = False
		self._window_state = 0

		# create menu items
		self.menu_bar = Gtk.MenuBar.new()

		menu_items = (
			{
				'label': _('_File'),
				'name': 'file',
				'submenu': (
					{
						'label': _('New _tab'),
						'name': 'new_tab',
						'type': 'image',
						'image': 'tab-new',
						'data': 'file',
						'path': '<Sunflower>/File/NewTab',
						'submenu': ()
					},
					{
						'type': 'separator',
					},
					{
						'label': _('Create _file'),
						'name': 'create_file',
						'type': 'image',
						'stock': Gtk.STOCK_NEW,
						'callback': self._command_create,
						'data': 'file',
						'path': '<Sunflower>/File/CreateFile',
					},
					{
						'label': _('Create _directory'),
						'name': 'create_directory',
						'type': 'image',
						'image': 'folder-new',
						'callback': self._command_create,
						'data': 'directory',
						'path': '<Sunflower>/File/CreateDirectory',
					},
					{
						'type': 'separator',
					},
					{
						'label': _('_Open'),
						'type': 'image',
						'stock': Gtk.STOCK_OPEN,
						'callback': self._command_open,
						'path': '<Sunflower>/File/Open',
					},
					{
						'label': _('Open in new ta_b'),
						'type': 'image',
						'image': 'tab-new',
						'callback': self._command_open_in_new_tab,
						'path': '<Sunflower>/File/OpenInNewTab',
					},
					{
						'type': 'separator',
					},
					{
						'label': _('_Properties'),
						'type': 'image',
						'stock': Gtk.STOCK_PROPERTIES,
						'callback': self._command_properties,
						'path': '<Sunflower>/File/Properties',
					},
					{
						'type': 'separator',
					},
					{
						'label': _('_Quit'),
						'name': 'quit_program',
						'type': 'image',
						'stock': Gtk.STOCK_QUIT,
						'callback' : self._quit,
						'path': '<Sunflower>/File/Quit'
					},
				)
			},
			{
				'label': _('_Edit'),
				'submenu': (
					{
						'label': _('Cu_t'),
						'type': 'image',
						'stock': Gtk.STOCK_CUT,
						'callback': self._command_cut_to_clipboard,
						'path': '<Sunflower>/Edit/Cut',
					},
					{
						'label': _('_Copy'),
						'type': 'image',
						'stock': Gtk.STOCK_COPY,
						'callback': self._command_copy_to_clipboard,
						'path': '<Sunflower>/Edit/Copy',
					},
					{
						'label': _('_Paste'),
						'type': 'image',
						'stock': Gtk.STOCK_PASTE,
						'callback': self._command_paste_from_clipboard,
						'path': '<Sunflower>/Edit/Paste',
					},
					{
						'type': 'separator',
					},
					{
						'label': _('_Delete'),
						'type': 'image',
						'stock': Gtk.STOCK_DELETE,
						'callback': self._command_delete,
						'path': '<Sunflower>/Edit/Delete',
					},
					{
						'type': 'separator',
					},
					{
						'label': _('_Send to...'),
						'name': 'send_to',
						'type': 'image',
						'image': 'document-send',
						'callback': self._command_send_to,
						'path': '<Sunflower>/Edit/SendTo',
						'visible': self.NAUTILUS_SEND_TO_INSTALLED,
					},
					{
						'label': _('Ma_ke link'),
						'name': 'make_link',
						'callback': self._create_link,
						'path': '<Sunflower>/Edit/MakeLink',
					},
					{
						'label': _('_Rename...'),
						'callback': self._command_rename,
						'path': '<Sunflower>/Edit/Rename',
					},
					{
						'type': 'separator',
					},
					{
						'label': _('_Unmount'),
						'name': 'unmount_menu',
						'submenu': (
							{
								'label': _('Mount list is empty'),
								'name': 'mount_list_empty',
							},
						),
					},
					{
						'type': 'separator',
					},
					{
						'label': _('_Preferences'),
						'name': 'show_preferences',
						'type': 'image',
						'stock': Gtk.STOCK_PREFERENCES,
						'callback': self.preferences_window._show,
						'path': '<Sunflower>/Edit/Preferences',
					},
				),
			},
			{
				'label': _('_Mark'),
				'submenu': (
					{
						'label': _('_Select all'),
						'type': 'image',
						'stock': Gtk.STOCK_SELECT_ALL,
						'callback': self.select_all,
						'path': '<Sunflower>/Mark/SelectAll',
					},
					{
						'label': _('_Deselect all'),
						'callback': self.deselect_all,
						'path': '<Sunflower>/Mark/DeselectAll',
					},
					{
						'label': _('Invert select_ion'),
						'callback': self.invert_selection,
						'path': '<Sunflower>/Mark/InvertSelection',
					},
					{'type': 'separator'},
					{
						'label': _('S_elect with pattern'),
						'name': 'select_with_pattern',
						'callback': self.select_with_pattern,
						'path': '<Sunflower>/Mark/SelectPattern',
					},
					{
						'label': _('Deselect with pa_ttern'),
						'name': 'deselect_with_pattern',
						'callback': self.deselect_with_pattern,
						'path': '<Sunflower>/Mark/DeselectPattern',
					},
					{'type': 'separator'},
					{
						'label': _('Select with same e_xtension'),
						'name': 'select_with_same_extension',
						'callback': self.select_with_same_extension,
						'path': '<Sunflower>/Mark/SelectWithSameExtension',
					},
					{
						'label': _('Deselect with same exte_nsion'),
						'name': 'deselect_with_same_extension',
						'callback': self.deselect_with_same_extension,
						'path': '<Sunflower>/Mark/DeselectWithSameExtension',
					},
					{'type': 'separator'},
					{
						'label': _('Compare _directories'),
						'name': 'compare_directories',
						'callback': self.compare_directories,
						'path': '<Sunflower>/Mark/Compare',
					}
				)
			},
			{
				'label': _('_Tools'),
				'name': 'tools',
				'submenu': (
					{
						'label': _('_Find files'),
						'name': 'find_files',
						'type': 'image',
						'image': 'system-search',
						'path': '<Sunflower>/Tools/FindFiles',
						'callback': self.show_find_files
					},
					{
						'label': _('Find _duplicate files'),
						'name': 'find_duplicate_files',
						'path': '<Sunflower>/Tools/FindDuplicateFiles'
					},
					{
						'label': _('_Synchronize directories'),
						'name': 'synchronize_directories',
						'path': '<Sunflower>/Tools/SynchronizeDirectories'
					},
					{'type': 'separator'},
					{
						'label': _('Advanced _rename'),
						'name': 'advanced_rename',
						'path': '<Sunflower>/Tools/AdvancedRename',
						'callback': self.show_advanced_rename,
					},
					{'type': 'separator'},
					{
						'label': _('_Keyring manager'),
						'name': 'keyring_manager',
						'path': '<Sunflower>/Tools/KeyringManager',
						'callback': self.show_keyring_manager,
					}
				)
			},
			{
				'label': _('_View'),
				'submenu': (
					{
						'label': _('Rel_oad item list'),
						'type': 'image',
						'image': 'reload',
						'callback': self._command_reload,
						'path': '<Sunflower>/View/Reload'
					},
					{
						'type': 'separator',
					},
					{
						'label': _('Fast m_edia preview'),
						'type': 'checkbox',
						'active': self.options.get('media_preview'),
						'callback': self._toggle_media_preview,
						'name': 'fast_media_preview',
						'path': '<Sunflower>/View/FastMediaPreview',
					},
					{
						'type': 'separator',
					},
					{
						'label': _('Show _hidden files'),
						'type': 'checkbox',
						'active': self.options.section('item_list').get('show_hidden'),
						'callback': self._toggle_show_hidden_files,
						'name': 'show_hidden_files',
						'path': '<Sunflower>/View/ShowHidden',
					},
					{
						'label': _('Show _toolbar'),
						'type': 'checkbox',
						'active': self.options.get('show_toolbar'),
						'callback': self._toggle_show_toolbar,
						'name': 'show_toolbar',
						'path': '<Sunflower>/View/ShowToolbar',
					},
					{
						'label': _('Show _command bar'),
						'type': 'checkbox',
						'active': self.options.get('show_command_bar'),
						'callback': self._toggle_show_command_bar,
						'name': 'show_command_bar',
						'path': '<Sunflower>/View/ShowCommandBar',
					},
					{
						'label': _('_Horizontal split'),
						'type': 'checkbox',
						'active': self.options.get('horizontal_split'),
						'callback': self._toggle_horizontal_split,
						'name': 'horizontal_split',
						'path': '<Sunflower>/View/HorizontalSplit',
					},
				)
			},
			{
				'label': _('_Commands'),
				'name': 'commands',
			},
			{
				'label': _('_Operations'),
				'name': 'operations',
				'submenu': (
					{
						'label': _('There are no active operations'),
						'name': 'no_operations',
					},
				)
			},
			{
				'label': _('_Help'),
				'submenu': (
					{
						'label': _('_Home page'),
						'type': 'image',
						'stock': Gtk.STOCK_HOME,
						'callback': self.goto_web,
						'data': 'sunflower-fm.org',
						'path': '<Sunflower>/Help/HomePage',
					},
					{
						'label': _('Check for new version'),
						'name': 'check_for_new_version',
						'callback': self.check_for_new_version,
						'path': '<Sunflower>/Help/CheckVersion',
					},
					{'type': 'separator'},
					{
						'label': _('File a _bug report'),
						'type': 'image',
						'image': 'lpi-bug',
						'callback': self.goto_web,
						'data': 'gitlab.com/MeanEYE/Sunflower/issues/new',
						'path': '<Sunflower>/Help/BugReport',
					},
					{'type': 'separator'},
					{
						'label': _('_About'),
						'type': 'image',
						'stock': Gtk.STOCK_ABOUT,
						'callback': self.show_about_window,
						'path': '<Sunflower>/Help/About',
					}
				)
			},
		)

		# create main menu accelerators group
		self.configure_accelerators(menu_items)

		# add items to main menu
		for item in menu_items:
			self.menu_bar.append(self.menu_manager.create_menu_item(item))

		# create actions
		action_list = (
					('tools.find_files', self.show_find_files),
					('tools.advanced_rename', self.show_advanced_rename),
					# ('tools.mount_manager', self.show_mounts),
					('tools.keyring_manager', self.show_keyring_manager),
					('help.home_page', self.goto_web),
					('help.check_version', self.check_for_new_version),
					('help.about', self.show_about_window),
					('preferences', self.preferences_window._show),
					('quit', self._quit)
				)

		for data in action_list:
			action = Gio.SimpleAction.new(data[0], None)
			action.connect('activate', data[1]);
			self.add_action(action)

		# create application menu
		self._application_menu = Gio.Menu.new()
		self._features_section = Gio.Menu.new()
		self._program_section = Gio.Menu.new()
		self._tools_menu = Gio.Menu.new()
		self._help_menu = Gio.Menu.new()

		self._tools_menu.append(_('_Find files'), 'win.tools.find_files')
		self._tools_menu.append(_('Advanced _rename'), 'win.tools.advanced_rename')
		self._tools_menu.append(_('_Mount manager'), 'win.tools.mount_manager')
		self._tools_menu.append(_('_Keyring manager'), 'win.tools.keyring_manager')

		self._help_menu.append(_('Home page'), 'win.help.home_page')
		self._help_menu.append(_('Check for new version'), 'win.help.check_version')
		self._help_menu.append(_('_About'), 'win.help.about')

		self._features_section.append_submenu(_('Tools'), self._tools_menu)
		self._features_section.append_submenu(_('Help'), self._help_menu)
		self._features_section.append(_('Preferences'), 'win.preferences')

		self._program_section.append(_('Quit'), 'win.quit')

		self._application_menu.append_section(None, self._features_section)
		self._application_menu.append_section(None, self._program_section)
		application.set_app_menu(self._application_menu)

		# commands menu
		self.menu_commands = Gtk.Menu()

		self._menu_item_commands = self.menu_manager.get_item_by_name('commands')
		self._menu_item_commands.set_submenu(self.menu_commands)

		# operations menu
		self._menu_item_operations = self.menu_manager.get_item_by_name('operations')
		self._menu_item_no_operations = self.menu_manager.get_item_by_name('no_operations')

		self.menu_operations = self._menu_item_operations.get_submenu()

		# create toolbar
		self.toolbar_manager.load_config(self.toolbar_options)
		self.toolbar_manager.apply_settings()

		toolbar = self.toolbar_manager.get_toolbar()
		toolbar.set_property('no-show-all', not self.options.get('show_toolbar'))

		# bookmarks menu
		self.locations = LocationMenu(self)

		# tools menu
		menu_item_tools = self.menu_manager.get_item_by_name('tools')
		self.menu_tools = menu_item_tools.get_submenu()

		# create notebooks
		self._paned = Gtk.VPaned() if self.options.get('horizontal_split') else Gtk.HPaned()

		self.left_notebook = Gtk.Notebook.new()
		self.left_notebook.set_scrollable(True)
		self.left_notebook.connect('focus-in-event', self._transfer_focus)
		self.left_notebook.connect('page-added', self._page_added)
		self.left_notebook.connect('switch-page', self._page_switched)
		self.left_notebook.set_group_name('panel')

		self.right_notebook = Gtk.Notebook.new()
		self.right_notebook.set_scrollable(True)
		self.right_notebook.connect('focus-in-event', self._transfer_focus)
		self.right_notebook.connect('page-added', self._page_added)
		self.right_notebook.connect('switch-page', self._page_switched)
		self.right_notebook.set_group_name('panel')

		self._paned.pack1(self.left_notebook, resize=True, shrink=False)
		self._paned.pack2(self.right_notebook, resize=True, shrink=False)

		# command line prompt
		self.command_popover = Gtk.Popover.new()
		self.command_popover.set_relative_to(self.header_bar)
		self.command_popover.set_modal(False)
		self.command_popover.connect('closed', self.hide_command_entry)

		vbox_popover = Gtk.VBox.new(False, 0)
		vbox_popover.set_border_width(5)
		vbox_popover.set_size_request(400, -1)

		self.status_bar = Gtk.HBox(False, 0)
		self.header_bar.pack_end(self.status_bar)

		label_command_entry = Gtk.Label(label=_('Execute command:'))
		label_command_entry.set_alignment(0, 0.5)
		label_command_entry.show()

		# create history list
		self.command_list = Gtk.ListStore(str)

		# create auto-complete entry
		self.command_completion = Gtk.EntryCompletion.new()
		self.command_completion.set_model(self.command_list)
		self.command_completion.set_minimum_key_length(2)
		self.command_completion.set_text_column(0)

		# create editor
		self.command_edit = Gtk.Entry.new()
		self.command_edit.set_completion(self.command_completion)
		self.command_edit.connect('activate', self.execute_command)
		self.command_edit.connect('key-press-event', self._command_edit_key_press)
		self.command_edit.connect('focus-in-event', self._command_edit_focused)
		self.command_edit.connect('focus-out-event', self._command_edit_lost_focus)
		self.command_edit.show()

		# load history file
		self._load_history()

		# pack command entry popover
		self.command_popover.add(vbox_popover)
		vbox_popover.pack_start(label_command_entry, False, False, 0)
		vbox_popover.pack_start(self.command_edit, True, True, 0)
		vbox_popover.show_all()

		# pack command entry bar
		if self.keyring_manager.is_available():
			self.status_bar.pack_start(self.keyring_manager._status_icon, False, False, 0)

		# command buttons bar
		self.command_bar = Gtk.HBox(True, 0)

		buttons = (
				(_('Refresh'), _('Reload active item list'), self._command_reload),
				(_('Rename'), _('Rename selected file'), self._command_rename),
				(_('Preview'), _('Preview selected file'), self._command_view),
				(_('Edit'), _('Edit selected file'), self._command_edit),
				(_('Copy'), _('Copy selected items from active to opposite list'), self._command_copy),
				(_('Move'), _('Move selected items from active to opposite list'), self._command_move),
				(_('Create'), _('Create new directory'), self._command_create),
				(_('Delete'), _('Delete selected items'), self._command_delete)
			)

		# create buttons and pack them
		for text, tooltip, callback in buttons:
			button = Gtk.Button(label=text)

			if callback is not None:
				button.connect('clicked', callback)

			button.set_tooltip_text(tooltip)
			button.set_focus_on_click(False)

			button.show()  # we need to explicitly show in cases where toolbar is not visible

			self.command_bar.pack_start(button, True, True, 0)

		self.command_bar.set_property('no-show-all', not self.options.get('show_command_bar'))

		# pack user interface
		vbox = Gtk.VBox(False, 0)
		# TODO: Get rid of this
		# vbox.pack_start(self.menu_bar, expand=False, fill=False, padding=0)
		vbox.pack_start(self.toolbar_manager.get_toolbar(), expand=False, fill=False, padding=0)

		self._vbox2 = Gtk.VBox(False, 4)
		self._vbox2.pack_start(self._paned, expand=True, fill=True, padding=0)
		self._vbox2.pack_start(self.command_bar, expand=False, fill=False, padding=0)

		vbox.pack_start(self._vbox2, True, True, 0)
		self.add(vbox)

		# create commands menu
		self._create_commands_menu()

		# restore window size and position
		self._restore_window_position()

		# load plugins
		self._load_plugins(dont_load_plugins)

		# create mount manager extensions
		self.mount_manager.create_extensions()

		# create toolbar widgets
		self.toolbar_manager.create_widgets()

		# activate accelerators
		self._accel_group.activate(self)

		# show widgets
		self.show_all()

	def _destroy(self, widget=None, data=None):
		"""Application destructor"""
		# save tabs
		self.save_tabs(self.left_notebook, 'left')
		self.save_tabs(self.right_notebook, 'right')

		# save window properties
		self._save_window_position()
		self._save_active_notebook()

		# terminate all disk usage threads
		self.disk_usage.cancel_all()

		# lock keyring
		self.keyring_manager.lock_keyring()

		# save config changes
		self.save_config()

		# TODO: Make sure all threads are stopped at this point.

	def _quit(self, widget=None, data=None):
		"""Trigger destory action from Quit menu item"""
		if not self.emit('delete-event', Gdk.Event.new(Gdk.EventType.DELETE)):
			self.destroy()

	def _delete_event(self, widget, data=None):
		"""Handle delete event"""
		self.hide()
		self.indicator.adjust_visibility_items(False)

		return True  # prevent default handler

	def _create_commands_menu(self):
		"""Create commands main menu"""
		self.commands_menu.remove_all()

		command_list = self.command_options.get('commands')

		for command_data in command_list:
			# create menu item
			if command_data['title'] != '-':
				# normal menu item
				tool = Gtk.MenuItem(label=command_data['title'])
				tool.connect('activate', self._handle_command_click)
				tool.command = command_data['command']

			else:
				# separator
				tool = Gtk.SeparatorMenuItem()

			# add item to the tools menu
			self.menu_commands.append(tool)

		# create separator
		if len(command_list) > 1:
			separator = Gtk.SeparatorMenuItem()
			self.menu_commands.append(separator)

		# create option for editing tools
		edit_commands = Gtk.ImageMenuItem.new_from_stock(Gtk.STOCK_PREFERENCES)
		edit_commands.set_label(_('_Edit commands'))
		edit_commands.connect('activate', self.preferences_window._show, 'commands')
		self.menu_commands.append(edit_commands)

		self._menu_item_commands.set_sensitive(True)
		self.menu_commands.show_all()

	def _add_bookmark(self, widget, item_list=None):
		"""Show dialog for adding a new bookmark"""
		if item_list is None:
			# no list was specified
			item_list = self.get_active_object()

		path = item_list.path
		dialog = AddBookmarkDialog(self, path)

		response = dialog.get_response()

		if response[0] == Gtk.ResponseType.OK:
			self.bookmark_options.get('bookmarks').append({
					'name': response[1],
					'uri': response[2]
				})

			self.locations.update_bookmarks()

	def _handle_command_click(self, widget, data=None):
		"""Handle click on command menu item"""
		command = widget.command

		# grab active objects
		left_object = self.get_left_object()
		right_object = self.get_right_object()

		if hasattr(left_object, '_get_selection'):
			# get selected item from the left list
			left_selection_short = left_object._get_selection(True)
			left_selection_long = left_object._get_selection(False)
			left_path_short = os.path.basename(left_object.path)
			left_path_long = left_object.path
			if not left_selection_short:
				left_selection_short = "."
				left_selection_long = left_object.path

		if hasattr(right_object, '_get_selection'):
			# get selected item from the left list
			right_selection_short = right_object._get_selection(True)
			right_selection_long = right_object._get_selection(False)
			right_path_short = os.path.basename(right_object.path)
			right_path_long = right_object.path
			if not right_selection_short:
				right_selection_short = "."
				right_selection_long = right_object.path

		# get universal 'selected item' values
		if self.get_active_object() is left_object:
			selection_short = left_selection_short
			selection_long = left_selection_long
			path_short = left_path_short
			path_long = left_path_long
			selection_list_short = left_object._get_selection_list(False, True)
			selection_list_long = left_object._get_selection_list(False, False)
			if not selection_list_short:
				selection_list_short = ['.']
				selection_list_long = [left_object.path]
		else:
			selection_short = right_selection_short
			selection_long = right_selection_long
			path_short = right_path_short
			path_long = right_path_long
			selection_list_short = right_object._get_selection_list(False, True)
			selection_list_long = right_object._get_selection_list(False, False)
			if not selection_list_short:
				selection_list_short = ['.']
				selection_list_long = [right_object.path]

		# replace command
		command = command.replace('%l', str(left_selection_short))
		command = command.replace('%L', str(left_selection_long))
		command = command.replace('%r', str(right_selection_short))
		command = command.replace('%R', str(right_selection_long))
		command = command.replace('%s', str(selection_short))
		command = command.replace('%S', str(selection_long))
		command = command.replace('%d', str(path_short))
		command = command.replace('%D', str(path_long))

		# TODO: Simplify this.
		if selection_list_short:
			command = command.replace('%m', '"' + '" "'.join(selection_list_short) + '"')

		if selection_list_short and (len(selection_list_short) > 1 or selection_list_short[0] != selection_short):
			command = command.replace('%u', '"' + '" "'.join(selection_list_short) + '"')
		else:
			command = command.replace('%u', '"' + left_selection_short + '" "' + right_selection_short + '"')

		if selection_list_long:
			command = command.replace('%M', '"' + '" "'.join(selection_list_long) + '"')

		if selection_list_long and (len(selection_list_long) > 1 or selection_list_long[0] != selection_long):
			command = command.replace('%U', '"' + '" "'.join(selection_list_long) + '"')
		else:
			command = command.replace('%U', '"' + left_selection_long + '" "' + right_selection_long + '"')

		# execute command using programs default handler
		self.execute_command(widget, command)

	def _handle_new_tab_click(self, widget, data=None):
		"""Handle clicking on item from 'New tab' menu"""
		notebook = self.get_active_object()._notebook
		plugin_class = widget.plugin_class

		self.create_tab(notebook, plugin_class)

	def _handle_configure_event(self, widget, event):
		"""Handle window resizing"""
		if self.get_state() == 0:
			self._geometry = self.get_size()

	def _handle_window_state_event(self, widget, event):
		"""Handle window state change"""
		self._in_fullscreen = bool(Gdk.WindowState.FULLSCREEN & event.new_window_state)
		self._window_state = event.new_window_state

	def _page_added(self, notebook, child, page_num):
		"""Handle adding/moving tab accross notebooks"""
		if hasattr(child, 'update_notebook'):
			child.update_notebook(notebook)

		if self.options.get('expand_tabs') == TabExpand.ALL:
			notebook.child_set_property(child, 'tab-expand', True)

		notebook.set_tab_reorderable(child, True)
		notebook.set_tab_detachable(child, True)

	def _page_switched(self, notebook, page, page_num, data=None):
		"""Handle switching pages"""
		current_page = notebook.get_nth_page(notebook.get_current_page())
		new_page = notebook.get_nth_page(page_num)

		if self.options.get('expand_tabs') == TabExpand.ACTIVE:
			notebook.child_set_property(current_page, 'tab-expand', False)
			notebook.child_set_property(new_page, 'tab-expand', True)

	def _transfer_focus(self, notebook, data=None):
		"""Transfer focus from notebook to child widget in active tab"""
		selected_page = notebook.get_nth_page(notebook.get_current_page())
		selected_page.focus_main_object()

	def _toggle_show_hidden_files(self, widget, data=None):
		"""Transfer option event to all the lists"""
		section = self.options.section('item_list')
		menu_item = self.menu_manager.get_item_by_name('show_hidden_files')

		# NOTE: Calling set_active emits signal causing deadloop,
		# to work around this issue we check if calling widget is menu item.
		if widget is menu_item:
			show_hidden = menu_item.get_active()
			section.set('show_hidden', show_hidden)

		else:
			menu_item.set_active(not section.get('show_hidden'))
			return True

		# update left notebook
		for index in range(0, self.left_notebook.get_n_pages()):
			page = self.left_notebook.get_nth_page(index)

			if hasattr(page, 'refresh_file_list'):
				page.refresh_file_list(widget, data)

		# update right notebook
		for index in range(0, self.right_notebook.get_n_pages()):
			page = self.right_notebook.get_nth_page(index)

			if hasattr(page, 'refresh_file_list'):
				page.refresh_file_list(widget, data)

		return True

	def _toggle_horizontal_split(self, widget=None, data=None):
		menu_item = self.menu_manager.get_item_by_name('horizontal_split')

		# NOTE: Calling set_active emits signal causing deadloop,
		# to work around this issue we check if calling widget is menu item.
		if widget is menu_item:
			horizontal_split = menu_item.get_active()
			self.options.set('horizontal_split', horizontal_split)

			self._paned.remove(self.left_notebook)
			self._paned.remove(self.right_notebook)
			self._vbox2.remove(self._paned)

			self._paned = Gtk.VPaned() if horizontal_split else Gtk.HPaned()
			self._paned.pack1(self.left_notebook, resize=True, shrink=False)
			self._paned.pack2(self.right_notebook, resize=True, shrink=False)

			self._vbox2.pack_start(self._paned, True, True, 0)
			self._vbox2.reorder_child(self._paned, 0)

			self._paned.show()
		else:
			menu_item.set_active(not self.options.get('horizontal_split'))

		return True

	def _toggle_show_command_bar(self, widget, data=None):
		"""Show/hide command bar"""
		menu_item = self.menu_manager.get_item_by_name('show_command_bar')

		# NOTE: Calling set_active emits signal causing deadloop,
		# to work around this issue we check if calling widget is menu item.
		if widget is menu_item:
			show_command_bar = menu_item.get_active()
			self.options.set('show_command_bar', show_command_bar)
			self.command_bar.set_visible(show_command_bar)

		else:
			menu_item.set_active(not self.options.get('show_command_bar'))

		return True

	def _toggle_show_toolbar(self, widget, data=None):
		"""Show/hide toolbar"""
		menu_item = self.menu_manager.get_item_by_name('show_toolbar')

		# NOTE: Calling set_active emits signal causing deadloop,
		# to work around this issue we check if calling widget is menu item.
		if widget is menu_item:
			show_toolbar = menu_item.get_active()
			self.options.set('show_toolbar', show_toolbar)
			self.toolbar_manager.get_toolbar().set_visible(show_toolbar)

		else:
			menu_item.set_active(not self.options.get('show_toolbar'))

		return True

	def _toggle_media_preview(self, widget, data=None):
		"""Enable/disable fast image preview"""
		menu_item = self.menu_manager.get_item_by_name('fast_media_preview')

		if widget is menu_item:
			self.options.set('media_preview', menu_item.get_active())

		else:
			menu_item.set_active(not self.options.get('media_preview'))
			return True

		# update left notebook
		for index in range(0, self.left_notebook.get_n_pages()):
			page = self.left_notebook.get_nth_page(index)

			if hasattr(page, 'apply_media_preview_settings'):
				page.apply_media_preview_settings()

		# update right notebook
		for index in range(0, self.right_notebook.get_n_pages()):
			page = self.right_notebook.get_nth_page(index)

			if hasattr(page, 'apply_media_preview_settings'):
				page.apply_media_preview_settings()

		return True

	def _set_active_object(self, new_object):
		"""Set active object"""
		if new_object is not None:
			self._active_object = new_object

	def _load_history(self):
		"""Load history file and populate the command list"""
		self.command_list.clear()

		try:
			# try to load our history file
			history_file = os.path.join(os.path.expanduser('~'), self.options.get('history_file'))

			# load history file
			with open(history_file, 'r') as raw_file:
				temp_list = raw_file.read().split('\n')

			# filter out duplicates
			temp_list = list(set(temp_list))

			# add commands to list
			for command in temp_list:
				self.command_list.append((command,))

		except:
			pass

	def _get_plugin_list(self):
		"""Get list of plugins"""
		user_path = Path(self.user_plugin_path)
		system_path = Path(self.system_plugin_path)

		# get list of system wide plugins
		plugin_list = [ plugin_dir.name for plugin_dir in system_path.iterdir()
						if plugin_dir.is_dir() and (plugin_dir/'plugin.py').exists() ]

		# get user specific plugins
		if user_path.is_dir():
			plugin_list += [ plugin_dir.name for plugin_dir in user_path.iterdir()
						     if plugin_dir.is_dir() and (plugin_dir/'plugin.py').exists() ]

		return plugin_list

	def _load_plugins(self, dont_load_plugins):
		"""Dynamically load plugins"""
		plugin_files = self._get_plugin_list()
		plugins_to_load = self.options.get('plugins')

		# make sure user plugin path is in module search path
		if self.config_path not in sys.path:
			sys.path.append(self.config_path)

		# only load protected plugins if command line parameter is specified
		if dont_load_plugins:
			plugins_to_load = self.protected_plugins

		to_load = partial(contains, plugins_to_load)

		for file_name in filter(to_load, plugin_files):
			try:
				# determine whether we need to load user plugin or system plugin
				user_plugin_exists = os.path.exists(os.path.join(self.user_plugin_path, file_name))
				load_user_plugin = user_plugin_exists and file_name not in self.protected_plugins

				plugin_base_module = 'user_plugins' if load_user_plugin else 'sunflower.plugins'

				# import module
				plugin = import_module('{0}.{1}.plugin'.format(plugin_base_module, file_name))

				# call module register_plugin method
				if hasattr(plugin, 'register_plugin'):
					plugin.register_plugin(self)

			except Exception as error:
				print('Error: Unable to load plugin "{0}": {1}'.format(file_name, error))

				# in case plugin is protected, complain and exit
				if file_name in self.protected_plugins:
					print('\nFatal error! Failed to load required plugin, exiting!')
					sys.exit(3)

	def _load_styles(self):
		"""Load custom application CSS styles."""
		provider = Gtk.CssProvider.new()
		screen = Gdk.Screen.get_default()

		# prepare path to load from
		base_path = os.path.dirname(sys.argv[0])
		file_name = os.path.join(base_path, 'styles', 'main.css')

		# load and apply style
		provider.load_from_file(Gio.File.new_for_path(file_name))
		Gtk.StyleContext.add_provider_for_screen(screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)

	def _command_reload(self, widget=None, data=None):
		"""Handle command button click"""
		result = False
		active_object = self.get_active_object()

		if hasattr(active_object, 'refresh_file_list'):
			active_object.refresh_file_list()
			result = True

		return result

	def _command_view(self, widget=None, data=None):
		"""Handle command button click"""
		result = False
		active_object = self.get_active_object()

		if hasattr(active_object, '_view_selected'):
			active_object._view_selected()
			result = True

		return result

	def _command_edit(self, widget=None, data=None):
		"""Handle command button click"""
		result = False
		active_object = self.get_active_object()

		if hasattr(active_object, '_edit_selected'):
			active_object._edit_selected()
			result = True

		return result

	def _command_copy(self, widget=None, data=None):
		"""Handle command button click"""
		result = False
		active_object = self.get_active_object()

		if hasattr(active_object, '_copy_files'):
			active_object._copy_files()
			result = True

		return result

	def _command_move(self, widget=None, data=None):
		"""Handle command button click"""
		result = False
		active_object = self.get_active_object()

		if hasattr(active_object, '_move_files'):
			active_object._move_files()
			result = True

		return result

	def _command_create(self, widget=None, data=None):
		"""Handle command button click"""
		result = False
		active_object = self.get_active_object()

		if data is None or (data is not None and data == 'directory'):
			# create directory
			if hasattr(active_object, '_create_directory'):
				active_object._create_directory()
				result = True

		else:
			# create file
			if hasattr(active_object, '_create_file'):
				active_object._create_file()
				result = True

		return result

	def _command_delete(self, widget=None, data=None):
		"""Handle command button click"""
		result = False
		active_object = self.get_active_object()

		if hasattr(active_object, '_delete_files'):
			active_object._delete_files()
			result = True

		return result

	def _command_open(self, widget=None, data=None):
		"""Execute selected item in active list"""
		result = False
		active_object = self.get_active_object()

		if hasattr(active_object, '_execute_selected_item'):
			active_object._execute_selected_item()
			result = True

		return result

	def _command_open_in_new_tab(self, widget=None, data=None):
		"""Open selected directory from active list in new tab"""
		result = False
		active_object = self.get_active_object()

		if hasattr(active_object, '_open_in_new_tab'):
			active_object._open_in_new_tab()
			result = True

		return result

	def _command_cut_to_clipboard(self, widget=None, data=None):
		"""Copy selected items from active list to clipboard"""
		result = False
		active_object = self.get_active_object()

		if hasattr(active_object, '_cut_files_to_clipboard'):
			active_object._cut_files_to_clipboard()
			result = True

		return result

	def _command_copy_to_clipboard(self, widget=None, data=None):
		"""Copy selected items from active list to clipboard"""
		result = False
		active_object = self.get_active_object()

		if hasattr(active_object, '_copy_files_to_clipboard'):
			# ItemList object
			active_object._copy_files_to_clipboard()
			result = True

		elif hasattr(active_object, '_copy_selection'):
			# Terminal object
			active_object._copy_selection()
			result = True

		return result

	def _command_paste_from_clipboard(self, widget=None, data=None):
		"""Copy selected items from active list to clipboard"""
		result = False
		active_object = self.get_active_object()

		if hasattr(active_object, '_paste_files_from_clipboard'):
			# ItemList object
			active_object._paste_files_from_clipboard()
			result = True

		elif hasattr(active_object, '_paste_selection'):
			# Terminal object
			active_object._paste_selection()
			result = True

		return result

	def _command_properties(self, widget=None, data=None):
		"""Show properties for selected item in active list"""
		result = False
		active_object = self.get_active_object()

		if hasattr(active_object, '_item_properties'):
			active_object._item_properties()
			result = True

		return result

	def _command_send_to(self, widget=None, data=None):
		"""Show 'send to' dialog for selected items in active list"""
		result = False
		active_object = self.get_active_object()

		if hasattr(active_object, '_send_to'):
			active_object._send_to()
			result = True

		return result

	def _create_link(self, widget=None, data=None):
		"""Show dialog for creating symbolic or hard links"""
		result = False
		active_object = self.get_active_object()

		if hasattr(active_object, '_create_link'):
			active_object._create_link()
			result = True

		return result

	def _command_rename(self, widget=None, data=None):
		"""Rename selected item in active list"""
		result = False
		active_object = self.get_active_object()

		if hasattr(active_object, '_rename_file'):
			active_object._rename_file()
			result = True

		return result

	def _command_edit_key_press(self, widget, event):
		"""Handle key press in command edit."""
		result = False

		if event.get_state() & Gtk.accelerator_get_default_mod_mask() == 0:
			# handle pressing down in command entry
			if event.keyval == Gdk.KEY_Down:
				self.get_active_object().focus_main_object()
				result = True

			# handle pressing escape in command entry
			elif event.keyval == Gdk.KEY_Escape:
				self.get_active_object().focus_main_object()
				self.hide_command_entry()
				result = True

		return result

	def _command_edit_focused(self, widget, event):
		"""Handle focusing command entry"""
		self.accelerator_manager.deactivate_scheduled_groups(widget)
		self._accel_group.deactivate()

	def _command_edit_lost_focus(self, widget, event):
		"""Handle command entry loosing focus"""
		self._accel_group.activate(self)

	def _save_window_position(self):
		"""Save window position to config"""
		section = self.window_options.section('main')
		window_state = 0

		if self._window_state & Gdk.WindowState.FULLSCREEN:
			# window is in fullscreen
			window_state = 2

		elif self._window_state & Gdk.WindowState.MAXIMIZED:
			# window is maximized
			window_state = 1

		self.window_options.section('main').set('state', window_state)

		# save window size and position
		section.set('geometry', self._geometry)

		# save handle position
		section.set('handle_position', self._paned.get_position())

	def _save_active_notebook(self):
		"""Save active notebook to config"""
		active_object = self.get_active_object()
		is_left = active_object._notebook is self.left_notebook

		self.options.set('active_notebook', (1, 0)[is_left])

	def _restore_window_position(self):
		"""Restore window position from config string"""
		section = self.window_options.section('main')

		# block event handlers
		self.handler_block_by_func(self._handle_configure_event)
		self.handler_block_by_func(self._handle_window_state_event)

		# restore window geometry
		geometry = section.get('geometry')
		if isinstance(geometry, list):
			self.set_default_size(*geometry)
			self._geometry = geometry

		# restore window state
		window_state = self.window_options.section('main').get('state')

		if window_state == 1:
			self.maximize()

		elif window_state == 2:
			self.fullscreen()

		# restore handle position
		if section.has('handle_position'):
			position = section.get('handle_position')

			if isinstance(position, int):
				self._paned.set_position(section.get('handle_position'))

		# restore event handlers
		self.handler_unblock_by_func(self._handle_configure_event)
		self.handler_unblock_by_func(self._handle_window_state_event)

	def activate_bookmark(self, widget=None, index=0):
		"""Activate bookmark by index"""
		path = None
		result = False
		active_object = self.get_active_object()

		# read all bookmarks
		bookmark_list = self.bookmark_options.get('bookmarks')

		# check if index is valid
		if index == 0:
			path = os.path.expanduser('~')

		elif index-1 < len(bookmark_list):
			bookmark = bookmark_list[index-1]
			path = bookmark['uri']

		# change path
		if path is not None and hasattr(active_object, 'change_path'):
			active_object.change_path(path)
			result = True

		return result

	def show_bookmarks_menu(self, reference=None, notebook=None):
		"""Position bookmarks menu properly and show it"""
		if notebook is not None:
			target_object = notebook.get_nth_page(notebook.get_current_page())
		else:
			target_object = self.get_active_object()

		# make sure we have reference object
		if reference is None:
			reference = target_object.locations_button

		# show locations menu
		self.locations.set_current(target_object)
		self.locations.show(reference)

		return True

	def select_all(self, widget, data=None):
		"""Select all items in active list"""
		result = False
		active_object = self.get_active_object()

		# ensure we don't make exception on terminal tabs
		if hasattr(active_object, 'select_all'):
			active_object.select_all()
			result = True

		return result

	def deselect_all(self, widget, data=None):
		"""Deselect all items in active list"""
		result = False
		active_object = self.get_active_object()

		# ensure we don't make exception on terminal tabs
		if hasattr(active_object, 'deselect_all'):
			active_object.deselect_all()
			result = True

		return result

	def invert_selection(self, widget, data=None):
		"""Invert selection in active list"""
		result = False
		active_object = self.get_active_object()

		if hasattr(active_object, 'invert_selection'):
			active_object.invert_selection()
			result = True

		return result

	def select_with_pattern(self, widget, data=None):
		"""Ask user for selection pattern and select matching items"""
		result = False
		active_object = self.get_active_object()

		if hasattr(active_object, 'select_all'):
			# create dialog
			dialog = InputDialog(self)

			dialog.set_title(_('Select items'))
			dialog.set_label(_('Selection pattern (eg.: *.jpg):'))
			dialog.set_text('*')

			# get response
			response = dialog.get_response()

			# commit selection
			if response[0] == Gtk.ResponseType.OK:
				active_object.select_all(response[1])

			result = True

		return result

	def select_with_same_extension(self, widget, data=None):
		"""Select all items with same extension in active list"""
		result = False
		active_object = self.get_active_object()

		if hasattr(active_object, '_get_selection') and hasattr(active_object, 'select_all'):
			selection = active_object._get_selection()

			if selection is not None\
			and os.path.splitext(selection)[1] != '':
				active_object.select_all('*{0}'.format(os.path.splitext(selection)[1]))

			result = True

		return result

	def deselect_with_same_extension(self, widget, data=None):
		"""Select all items with same extension in active list"""
		result = False
		active_object = self.get_active_object()

		if hasattr(active_object, '_get_selection') and hasattr(active_object, 'select_all'):
			selection = active_object._get_selection()

			if selection is not None\
			and os.path.splitext(selection)[1] != '':
				active_object.deselect_all('*{0}'.format(os.path.splitext(selection)[1]))

			result = True

		return result

	def deselect_with_pattern(self, widget, data=None):
		"""Ask user for selection pattern and select matching items"""
		result = False
		active_object = self.get_active_object()

		if hasattr(active_object, 'deselect_all'):
			# create dialog
			dialog = InputDialog(self)

			dialog.set_title(_('Deselect items'))
			dialog.set_label(_('Selection pattern (eg.: *.jpg):'))
			dialog.set_text('*')

			# get response
			response = dialog.get_response()

			# commit selection
			if response[0] == Gtk.ResponseType.OK:
				active_object.deselect_all(response[1])

			result = True

		return result

	def compare_directories(self, widget=None, data=None):
		"""Compare directories from left and right notebook"""
		result = False
		left_object = self.get_left_object()
		right_object = self.get_right_object()

		# if both objects have selection methods and exist
		if hasattr(left_object, 'select_all') and hasattr(right_object, 'select_all'):
			# get file lists
			left_list = left_object.get_provider().list_dir(left_object.path)
			right_list = right_object.get_provider().list_dir(right_object.path)

			# mark missing files
			result_left = left_object.select_all(exclude_list=right_list)
			result_right = right_object.select_all(exclude_list=left_list)

			if result_left == result_right == 0:
				dialog = Gtk.MessageDialog(
										self,
										Gtk.DialogFlags.DESTROY_WITH_PARENT,
										Gtk.MessageType.INFO,
										Gtk.ButtonsType.OK,
										_("First level of compared directories is identical.")
									)
				dialog.run()
				dialog.destroy()

			result = True

		return result

	def create_tabs(self, arguments):
		"""Create all tabs taking into all (local or remote) account command line arguments"""
		if not arguments.is_remote:
			section = self.options.section('item_list')
			config_prevents_load = section.get('force_directories')
			arguments_prevents_load = arguments is not None and arguments.dont_load_tabs

			# load saved tabs if needed
			if not (config_prevents_load or arguments_prevents_load):
				self.load_tabs(self.left_notebook, 'left')
				self.load_tabs(self.right_notebook, 'right')

		left_list = []
		right_list = []

		DefaultList = self.plugin_classes['file_list']
		DefaultTerminal = self.plugin_classes['system_terminal']

		# populate lists with command line arguments
		if arguments is not None:
			if arguments.left_tabs is not None:
				left_list.extend(map(lambda path: (DefaultList, path), arguments.left_tabs))

			if arguments.right_tabs is not None:
				right_list.extend(map(lambda path: (DefaultList, path), arguments.right_tabs))

			if arguments.left_terminals is not None:
				left_list.extend(map(lambda path: (DefaultTerminal, path), arguments.left_terminals))

			if arguments.right_terminals is not None:
				right_list.extend(map(lambda path: (DefaultTerminal, path), arguments.right_terminals))

		# finally create additional tabs
		for Class, path in left_list:
			options = Parameters()
			options.set('path', path)
			self.create_tab(self.left_notebook, Class, options)

		for Class, path in right_list:
			options = Parameters()
			options.set('path', path)
			self.create_tab(self.right_notebook, Class, options)

		# make sure we have at least one tab loaded on each notebook
		if self.left_notebook.get_n_pages() == 0:
			self.create_tab(self.left_notebook, DefaultList)

		if self.right_notebook.get_n_pages() == 0:
			self.create_tab(self.right_notebook, DefaultList)

		if not arguments.is_remote:
			# focus active notebook
			active_notebook_index = self.options.get('active_notebook')
			notebook = (self.left_notebook, self.right_notebook)[active_notebook_index]
			notebook.grab_focus()

	def create_tab(self, notebook, plugin_class=None, options=None):
		"""Safe create tab"""
		if options is None:
			options = Parameters()

		# create plugin object
		new_tab = plugin_class(self, notebook, options)

		# add page to notebook
		index = notebook.append_page(new_tab, new_tab.get_tab_label())

		# show tabs if needed
		if not self.options.get('always_show_tabs'):
			notebook.set_show_tabs(notebook.get_n_pages() > 1)

		# focus tab if needed
		if self.options.get('focus_new_tab'):
			notebook.set_current_page(index)
			new_tab.focus_main_object()

		return new_tab

	def create_terminal_tab(self, notebook, options=None):
		"""Create terminal tab on selected notebook"""
		result = None

		if options is None:
			options = Parameters()

		shell_command = options.get('shell_command', None)
		command_version = 'command' if shell_command is None else 'command2'
		terminal_command = self.options.section('terminal').get(command_version)
		terminal_type = self.options.section('terminal').get('type')
		open_in_tab = not (terminal_type == TerminalType.EXTERNAL and '{0}' not in terminal_command)

		if open_in_tab:
			# create new terminal tab
			SystemTerminal = self.plugin_classes['system_terminal']
			result = self.create_tab(notebook, SystemTerminal, options)

		else:
			# open external terminal application
			try:
				path = options.get('path')

				# prepare environment
				environment = os.environ
				environment['PWD'] = path

				terminal_command = shlex.split(terminal_command.format('', shell_command, path))
				subprocess.Popen(terminal_command, cwd=path, env=environment)

			except:
				dialog = Gtk.MessageDialog(
										self,
										Gtk.DialogFlags.DESTROY_WITH_PARENT,
										Gtk.MessageType.ERROR,
										Gtk.ButtonsType.OK,
										_(
											'There was a problem starting external '
											'terminal application. Check if command '
											'is valid!'
										)
									)
				dialog.run()
				dialog.destroy()

		return result

	def close_tab(self, notebook, child, can_close_all=False):
		"""Safely remove tab and its children"""
		if (not can_close_all and notebook.get_n_pages() > 1 and not child.is_tab_locked()) or can_close_all:
			# call tab close handle method
			if hasattr(child, '_handle_tab_close'):
				child._handle_tab_close()

			# remove page from notebook
			notebook.remove_page(notebook.page_num(child))

			# hide tabs if needed
			if not self.options.get('always_show_tabs'):
				notebook.set_show_tabs(notebook.get_n_pages() > 1)

			# block signal when destroying plugin with columns
			if hasattr(child, '_column_changed'):
				child._item_list.handler_block_by_func(child._column_changed)

			# kill the component
			child.destroy()

	def close_all_tabs(self, notebook, excluded=None):
		tabs = notebook.get_children()
		for tab in tabs:
			if tab.is_tab_locked() or tab is excluded:
				continue
			self.close_tab(notebook, tab)

	def next_tab(self, notebook):
		"""Select next tab on given notebook"""
		first_page = 0
		last_page = notebook.get_n_pages() - 1

		if notebook.get_current_page() == last_page:
			self.set_active_tab(notebook, first_page)
		else:
			notebook.next_page()

		page = notebook.get_nth_page(notebook.get_current_page())
		page.focus_main_object()

	def previous_tab(self, notebook):
		"""Select previous tab on given notebook"""
		first_page = 0
		last_page = notebook.get_n_pages() - 1

		if notebook.get_current_page() == first_page:
			self.set_active_tab(notebook, last_page)
		else:
			notebook.prev_page()

		page = notebook.get_nth_page(notebook.get_current_page())
		page.focus_main_object()

	def set_active_tab(self, notebook, tab):
		"""Set active tab number"""
		notebook.set_current_page(tab)

	def set_location_label(self, path):
		"""Set location label"""
		self.header_bar.set_subtitle(path)

	def goto_web(self, widget, uri):
		"""Open URL stored in data"""
		if '://' in uri:
			webbrowser.open_new_tab(uri)

		else:
			webbrowser.open_new_tab('http://%s' % uri)

		return True

	def execute_command(self, widget, data=None):
		"""Executes system command"""
		if data is not None:
			# process custom data
			raw_command = data

		else:
			# get command from command entry
			raw_command = self.command_edit.get_text()
			self.command_edit.set_text('')
			self.hide_command_entry()

		handled = False
		active_object = self.get_active_object()
		command = shlex.split(raw_command)

		if command[0] == 'cd' and hasattr(active_object, 'change_path'):
			# handle change directory command
			path = command[1] if len(command) >= 2 else os.path.expanduser('~')

			# apply path modifications
			if path[0] == '~':
				path = os.path.expanduser(path)

			elif path[0] != os.sep:
				path = os.path.join(active_object.path, path)

			# if resulting path is a directory, change
			if active_object.get_provider().is_dir(path):
				active_object.change_path(path)
				active_object.focus_main_object()

			handled = True

		if not handled:
			# try executing command
			try:
				if common.is_gui_app(command[0]):
					# command is X based, just execute it
					process = subprocess.Popen(shlex.split(raw_command), cwd=active_object.path)

				else:
					# command is console based, create terminal tab and fork it
					options = Parameters()
					options.set('close_with_child', False)
					options.set('shell_command', command[0])
					options.set('arguments', command)
					options.set('path', active_object.path)

					self.create_terminal_tab(active_object._notebook, options)

				handled = True

			except OSError:
				handled = False

		if not handled:
			print('Unhandled command: {0}'.format(command[0]))

		return True

	def save_tabs(self, notebook, section):
		"""Save opened tabs"""
		tab_list = []

		for index in range(0, notebook.get_n_pages()):
			page = notebook.get_nth_page(index)

			# give plugin a chance to clean up
			if hasattr(page, '_handle_tab_close'):
				page._handle_tab_close()

			# get options from tab
			tab = page._options.get_params()
			tab['class'] = page._name

			# add tab to list
			tab_list.append(tab)

		# store tabs to configuration
		section = self.tab_options.create_section(section)

		section.set('tabs', tab_list)
		section.set('active_tab', notebook.get_current_page())

	def load_tabs(self, notebook, section):
		"""Load saved tabs"""
		result = False
		count = 0

		if self.tab_options.has_section(section):
			tab_list = self.tab_options.section(section).get('tabs')

			for tab in tab_list:
				if self.plugin_class_exists(tab['class']):
					# create new tab with specified data
					tab_class = tab['class']

					# prepare stored options
					options = tab.copy()
					del options['class']

					# create new tab
					self.create_tab(notebook, globals()[tab_class], Parameters(options))
					count += 1

				else:
					# print error to console
					print('Warning: Unknown plugin class "{0}". Tab skipped!'.format(tab['class']))

			result = count > 0

			# set active tab
			if result:
				active_tab = self.tab_options.section(section).get('active_tab')
				self.set_active_tab(notebook, active_tab)

		return result

	def configure_accelerators(self, menu):
		"""Configure main accelerators group"""
		group = AcceleratorGroup(self)
		keyval = Gdk.keyval_from_name
		required_fields = set(('label', 'callback', 'path', 'name'))

		# configure accelerator group
		group.set_name('main_menu')
		group.set_title(_('Main Menu'))

		# default accelerator map
		default_accelerator = {
				'<Sunflower>/File/CreateFile': (keyval('F7'), Gdk.ModifierType.CONTROL_MASK),
				'<Sunflower>/File/CreateDirectory': (keyval('F7'), 0),
				'<Sunflower>/File/Quit': (keyval('Q'), Gdk.ModifierType.CONTROL_MASK),
				'<Sunflower>/Edit/Preferences': (keyval('P'), Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK),
				'<Sunflower>/Mark/SelectPattern': (keyval('KP_Add'), 0),
				'<Sunflower>/Mark/DeselectPattern': (keyval('KP_Subtract'), 0),
				'<Sunflower>/Mark/SelectWithSameExtension': (keyval('KP_Add'), Gdk.ModifierType.MOD1_MASK),
				'<Sunflower>/Mark/DeselectWithSameExtension': (keyval('KP_Subtract'), Gdk.ModifierType.MOD1_MASK),
				'<Sunflower>/Mark/Compare': (keyval('F12'), 0),
				'<Sunflower>/Tools/FindFiles': (keyval('F7'), Gdk.ModifierType.MOD1_MASK),
				'<Sunflower>/Tools/SynchronizeDirectories': (keyval('F8'), Gdk.ModifierType.MOD1_MASK),
				'<Sunflower>/Tools/AdvancedRename': (keyval('M'), Gdk.ModifierType.CONTROL_MASK),
				'<Sunflower>/Tools/MountManager': (keyval('O'), Gdk.ModifierType.CONTROL_MASK),
				'<Sunflower>/View/Fullscreen': (keyval('F11'), 0),
				'<Sunflower>/View/Reload': (keyval('R'), Gdk.ModifierType.CONTROL_MASK),
				'<Sunflower>/View/FastMediaPreview': (keyval('F3'), Gdk.ModifierType.MOD1_MASK),
				'<Sunflower>/View/ShowHidden': (keyval('H'), Gdk.ModifierType.CONTROL_MASK),
			}

		alternative_accelerator = {
				'<Sunflower>/Mark/SelectPattern': (keyval('equal'), 0),
				'<Sunflower>/Mark/DeselectPattern': (keyval('minus'), 0),
				'<Sunflower>/Mark/SelectWithSameExtension': (keyval('equal'), Gdk.ModifierType.MOD1_MASK),
				'<Sunflower>/Mark/DeselectWithSameExtension': (keyval('minus'), Gdk.ModifierType.MOD1_MASK),
			}

		# filter out menu groups without submenu
		menu = filter(lambda menu_group: 'submenu' in menu_group, menu)

		# generate group based on main menu structure
		for menu_group in menu:
			group_name = menu_group['label'].replace('_', '')

			for menu_item in menu_group['submenu']:
				fields = set(menu_item.keys())

				if required_fields.issubset(fields):
					path = menu_item['path']
					label = u'{0} {1} {2}'.format(
							group_name,
							u'\u2192',
							menu_item['label'].replace('_', '')
						)
					callback = menu_item['callback']
					method_name = menu_item['name']
					data = menu_item['data'] if 'data' in menu_item else None

					# add method
					group.add_method(method_name, label, callback, data)

					# add default accelerator
					if path in default_accelerator:
						group.set_accelerator(method_name, *default_accelerator[path])

					# add alternative accelerator
					if path in alternative_accelerator:
						group.set_alt_accelerator(method_name, *alternative_accelerator[path])

					# set method path
					group.set_path(method_name, path)

		# add other methods
		group.add_method('restore_handle_position', _('Restore handle position'), self.restore_handle_position)
		group.add_method('move_handle_left', _('Move handle to the left'), self.move_handle, -1)
		group.add_method('move_handle_right', _('Move handle to the right'), self.move_handle, 1)

		# set default accelerators
		group.set_accelerator('restore_handle_position', keyval('Home'), Gdk.ModifierType.MOD1_MASK)
		group.set_accelerator('move_handle_left', keyval('Page_Up'), Gdk.ModifierType.MOD1_MASK)
		group.set_accelerator('move_handle_right', keyval('Page_Down'), Gdk.ModifierType.MOD1_MASK)

		# expose object
		self._accel_group = group

	def save_config(self):
		"""Save configuration to file"""
		try:
			# make sure config directory
			if not os.path.isdir(self.config_path):
				os.makedirs(self.config_path)

			# make sure plugins directory is present
			if not os.path.isdir(self.user_plugin_path):
				os.makedirs(self.user_plugin_path)
				open(os.path.join(self.user_plugin_path, '__init__.py'), 'w').close()

			self.options.save()
			self.window_options.save()
			self.plugin_options.save()
			self.tab_options.save()
			self.bookmark_options.save()
			self.toolbar_options.save()
			self.command_options.save()
			self.accel_options.save()
			self.association_options.save()
			self.mount_options.save()

			# save accelerators
			self.accelerator_manager.save()

		except IOError as error:
			# notify user about failure
			dialog = Gtk.MessageDialog(
									self,
									Gtk.DialogFlags.DESTROY_WITH_PARENT,
									Gtk.MessageType.ERROR,
									Gtk.ButtonsType.OK,
									_(
										'Error saving configuration to files '
										'in your home directory. Make sure you have '
										'enough permissions.'
									) +	'\n\n{0}'.format(error)
								)
			dialog.run()
			dialog.destroy()

	def load_config(self):
		"""Load configuration from file located in users home directory"""
		self.config_path = common.get_config_path()

		# generate plugins paths
		self.user_plugin_path = os.path.join(self.config_path, 'user_plugins')
		self.system_plugin_path = os.path.join(os.path.dirname(sys.argv[0]), 'plugins')

		# create config parsers
		self.options = Config('config', self.config_path)
		self.window_options = Config('windows', self.config_path)
		self.plugin_options = Config('plugins', self.config_path)
		self.tab_options = Config('tabs', self.config_path)
		self.bookmark_options = Config('bookmarks', self.config_path)
		self.toolbar_options = Config('toolbar', self.config_path)
		self.command_options = Config('commands', self.config_path)
		self.accel_options = Config('accelerators', self.config_path)
		self.association_options = Config('associations', self.config_path)
		self.mount_options = Config('mounts', self.config_path)

		# load accelerators
		self.accelerator_manager.load(self.accel_options)

		# create default main window options
		self.window_options.create_section('main').update({
					'geometry': '960x550',
					'state': 0,
					'hide_on_close': False,
					"handle_position": 480,
				})

		# create default terminal options
		self.options.create_section('terminal').update({
					'show_scrollbars': True,
					'command': 'xterm -into {0}',
					'command2': 'xterm -into {0} -e "{1}"',
					'type': 0,
					'cursor_shape': 0,
					'use_system_font': True,
					'font': 'Monospace 12',
					'allow_bold': True,
					'mouse_autohide': False
				})

		# create item list section
		self.options.create_section('item_list').update({
					'show_hidden': False,
					'search_modifier': '000',
					'time_format': '%H:%M %d-%m-%y',
					'row_hinting': False,
					'grid_lines': 0,
					'selection_color': '#ffff5e5e0000',
					'selection_indicator': u'\u2731',
					'case_sensitive_sort': True,
					'number_sensitive_sort': False,
					'right_click_select': False,
					'single_click_navigation': False,
					'headers_visible': True,
					'mode_format': 1,
					'left_directories': [],
					'right_directories': [],
					'force_directories': False,
					'show_expanders': False,
					'hide_horizontal_scrollbar': False,
					'second_extension': False,
					'always_visible': []
				})

		# create default operation options
		self.options.create_section('operations').update({
					'set_owner': False,
					'set_mode': True,
					'set_timestamp': True,
					'silent': False,
					'merge_in_silent': True,
					'overwrite_in_silent': True,
					'hide_on_minimize': False,
					'trash_files': True,
					'reserve_size': False,
					'automount_start': False,
					'automount_insert': False,
					'follow_symlink': False
				})

		# create default create file/directory dialog options
		self.options.create_section('create_dialog').update({
					'file_mode': 0o644,
					'directory_mode': 0o755,
					'edit_file': False
				})

		# create default confirmation options
		self.options.create_section('confirmations').update({
					'delete_items': True
				})

		# create default editor options
		default_application = self.associations_manager.get_default_application_for_type('text/plain')
		editor_command = None
		editor_name = None

		if default_application is not None:
			editor_command = default_application.command_line
			editor_name = default_application.name

		self.options.create_section('editor').update({
					'type': 0,
					'default_editor': editor_command,
					'external_command': editor_command,
					'application': editor_name,
					'terminal_command': False
				})

		# create default viewer options
		self.options.create_section('viewer').update({
					'word_wrap': False
				})

		# create default options for bookmarks
		self.bookmark_options.update({
					'add_home': True,
					'show_mounts': True,
					'system_bookmarks': False,
					'bookmarks': []
				})

		# set default application options
		self.options.update({
					'plugins': ['file_list', 'system_terminal', 'default_toolbar'],
					'show_toolbar': False,
					'show_command_bar': False,
					'history_file': '.bash_history',
					'last_version': 0,
					'focus_new_tab': True,
					'always_show_tabs': True,
					'expand_tabs': 0,
					'show_notifications': True,
					'ubuntu_coloring': False,  # TODO: Remove, outdated
					'superuser_notification': True,
					'tab_close_button': True,
					'show_status_bar': 0,
					'media_preview': False,
					'active_notebook': 0,
					'size_format': common.SizeFormat.SI,
					'multiple_instances': False,
					'network_path_completion': True,
					'horizontal_split': False
				})

		# set default commands
		self.command_options.update({
					'commands': []
				})

		# create default toolbar options
		self.toolbar_options.update({
					'style': 3,
					'icon_size': 1,
				})

	def restore_handle_position(self, widget=None, data=None):
		"""Restore handle position"""
		left_allocation = self.left_notebook.get_allocation()
		right_allocation = self.right_notebook.get_allocation()

		if self.options.get('horizontal_split'):
			left_size = left_allocation.height
			right_size = right_allocation.height

		else:
			left_size = left_allocation.width
			right_size = right_allocation.width

		# calculate middle position
		new_position = (left_size + right_size) / 2
		self._paned.set_position(new_position)
		return True

	def move_handle(self, widget=None, direction=1):
		"""Move handle to specified direction """
		new_position = self._paned.get_position() + (direction * 5)
		self._paned.set_position(new_position)
		return True

	def focus_opposite_object(self, widget, data=None):
		"""Sets focus on opposite item list"""
		opposite_object = self.get_opposite_object(self.get_active_object())
		opposite_object.focus_main_object()
		return True

	def show_command_entry(self, widget=None, data=None):
		"""Show command entry popover and set focus to it."""
		self.command_popover.popup()
		self.command_edit.grab_focus()
		return True

	def hide_command_entry(self, widget=None, data=None):
		"""Hide command entry popover."""
		self.command_popover.popdown()
		return True

	def focus_left_object(self, widget=None, data=None):
		"""Focus object in the left notebook"""
		left_object = self.get_left_object()
		left_object.focus_main_object()
		return True

	def focus_right_object(self, widget=None, data=None):
		"""Focus object in the right notebook"""
		right_object = self.get_right_object()
		right_object.focus_main_object()
		return True

	def get_active_object(self):
		"""Return active object"""
		return self._active_object

	def get_opposite_object(self, active_object):
		"""Return opposite object"""
		left_object = self.get_left_object()
		right_object = self.get_right_object()

		result = right_object if active_object is left_object else left_object

		return result

	def get_left_object(self):
		"""Return active tab from left notebook"""
		return self.left_notebook.get_nth_page(self.left_notebook.get_current_page())

	def get_right_object(self):
		"""Return active tab from right notebook"""
		return self.right_notebook.get_nth_page(self.right_notebook.get_current_page())

	def get_opposite_notebook(self, notebook):
		"""Return opposite notebook"""
		return self.left_notebook if notebook is self.right_notebook else self.right_notebook

	def delegate_to_objects(self, caller, method_name, *args):
		"""Call specified method_name on all active objects of same class as caller

		Params:
		caller - object
		method_name - string
		args - arguments to be passed to specified methods

		"""
		# get all objects
		objects = self.left_notebook.get_children()
		objects.extend(self.right_notebook.get_children())

		# get only objects of specified class that are not caller
		filter_objects = lambda item: item.__class__ is caller.__class__ and item is not caller
		objects = filter(filter_objects, objects)

		# call specified method_name
		for item in objects:
			method = getattr(item, method_name)

			if callable(method):
				method(*args)

	def add_operation(self, widget, callback, data=None):
		"""Add operation to menu"""
		item = Gtk.ImageMenuItem()
		item.add(widget)
		item.connect('activate', callback, data)

		item.show_all()
		item.hide()

		self.menu_operations.append(item)

		return item

	def remove_operation(self, widget):
		"""Remove operation item from menu"""
		self.menu_operations.remove(widget)
		self.operation_menu_changed()

	def operation_menu_changed(self):
		"""Increase count of visible operation menu items"""
		has_operations = False

		# check if there are minimized operations
		for operation_item in self.menu_operations.get_children():
			if operation_item is not self._menu_item_no_operations:
				has_operations = True
				break

		# set "no operations" menu item visibility
		self._menu_item_no_operations.set_visible(not has_operations)

	def apply_settings(self):
		"""Apply settings to all the pluggins and main window"""
		# show or hide command bar depending on settings
		show_command_bar = self.menu_manager.get_item_by_name('show_command_bar')
		show_command_bar.set_active(self.options.get('show_command_bar'))

		# show or hide toolbar depending on settings
		show_toolbar = self.menu_manager.get_item_by_name('show_toolbar')
		show_toolbar.set_active(self.options.get('show_toolbar'))

		# show or hide hidden files
		show_hidden = self.menu_manager.get_item_by_name('show_hidden_files')
		show_hidden.set_active(self.options.section('item_list').get('show_hidden'))

		# apply media preview settings
		media_preview = self.menu_manager.get_item_by_name('fast_media_preview')
		media_preview.set_active(self.options.get('media_preview'))

		# horizontal split
		horizontal_split = self.menu_manager.get_item_by_name('horizontal_split')
		horizontal_split.set_active(self.options.get('horizontal_split'))

		# recreate bookmarks menu
		self.locations.update_bookmarks()

		# recreate tools menu
		self._create_commands_menu()

		# recreate toolbar widgets
		self.toolbar_manager.apply_settings()
		self.toolbar_manager.create_widgets()

		# show tabs if needed
		self.left_notebook.set_show_tabs(
								self.options.get('always_show_tabs') or
								self.left_notebook.get_n_pages() > 1
							)
		self.right_notebook.set_show_tabs(
								self.options.get('always_show_tabs') or
								self.right_notebook.get_n_pages() > 1
							)

		# apply settings for all tabs
		expand_tabs = self.options.get('expand_tabs')

		for index in range(0, self.left_notebook.get_n_pages()):
			page = self.left_notebook.get_nth_page(index)

			# apply tab-expand
			if expand_tabs == TabExpand.NONE:
				self.left_notebook.child_set_property(page, 'tab-expand', False)

			elif expand_tabs == TabExpand.ACTIVE:
				self.left_notebook.child_set_property(page, 'tab-expand', page is self.get_active_object())

			else:
				self.left_notebook.child_set_property(page, 'tab-expand', True)

			# call plugin apply_settings
			if hasattr(page, 'apply_settings'):
				page.apply_settings()

		for index in range(0, self.right_notebook.get_n_pages()):
			page = self.right_notebook.get_nth_page(index)

			# apply tab-expand
			if expand_tabs == TabExpand.NONE:
				self.right_notebook.child_set_property(page, 'tab-expand', False)

			elif expand_tabs == TabExpand.ACTIVE:
				self.right_notebook.child_set_property(page, 'tab-expand', page is self.get_active_object())

			else:
				self.right_notebook.child_set_property(page, 'tab-expand', True)

			# call plugin apply_settings
			if hasattr(page, 'apply_settings'):
				page.apply_settings()

	def register_class(self, name, title, PluginClass):
		"""Register plugin class

		Classes registered using this method will be displayed in 'New tab' menu.
		Only plugins that provide tab components should be registered using this method!

		"""
		# add to plugin list
		self.plugin_classes[name] = PluginClass

		# create menu item and add it
		menu_item = Gtk.MenuItem(title)
		menu_item.plugin_class = PluginClass
		menu_item.connect('activate', self._handle_new_tab_click)

		menu_item.show()

		# add menu item
		menu = self.menu_manager.get_item_by_name('new_tab').get_submenu()
		menu.append(menu_item)

		# create action
		action = Gio.SimpleAction.new(name, None)
		action.plugin_class = PluginClass
		action.connect('activate', self._handle_new_tab_click)

		self.new_tab_menu.append(title, 'new-tab.{0}'.format(name))
		self.new_tab_actions.add_action(action)

		# import class to globals
		globals()[PluginClass.__name__] = PluginClass

	def register_provider(self, ProviderClass):
		"""Register file provider class for specified protocol

		These classes will be used when handling all sorts of URI based operations
		like drag and drop and system bookmark handling.

		"""
		self.provider_classes[ProviderClass.protocol] = ProviderClass

		for archive_type in ProviderClass.archives:
			self.archive_provider_classes[archive_type] = ProviderClass

	def register_toolbar_factory(self, FactoryClass):
		"""Register and create toolbar widget factory"""
		self.toolbar_manager.register_factory(FactoryClass)

	def register_rename_extension(self, name, ExtensionClass):
		"""Register class to be used in advanced rename tool"""
		if issubclass(ExtensionClass, RenameExtension) \
		and not name in self.rename_extension_classes:
			# register class
			self.rename_extension_classes[name] = ExtensionClass

		else:
			# report error to console
			if name in self.rename_extension_classes:
				print('Error: Extension with name "{0}" is already registered!')

			if not issubclass(ExtensionClass, RenameExtension):
				print('Error: Invalid object class!')

	def register_find_extension(self, name, ExtensionClass):
		"""Register class to be used in find files tool"""
		if issubclass(ExtensionClass, FindExtension) \
		and not name in self.find_extension_classes:
			# register extension
			self.find_extension_classes[name] = ExtensionClass

		else:
			# report error to console
			if name in self.find_extension_classes:
				print('Error: Extension with name "{0}" is already registered!')

			if not issubclass(ExtensionClass, FindExtension):
				print('Error: Invalid object class!')

	def register_mount_manager_extension(self, ExtensionClass):
		"""Register mount manager extension"""
		self.mount_manager_extensions.append(ExtensionClass)

	def register_column_extension(self, ListClass, ExtensionClass):
		"""Register column extension class"""
		self.column_extension_classes.append((ListClass, ExtensionClass))

	def register_column_editor_extension(self, extension):
		"""Register column editor extension"""
		self.column_editor_extensions.append(extension)

	def register_popup_menu_action(self, mime_types, menu_item):
		"""Register handler method for popup menu which will be
		displayed if file type matches any string in mime_types.

		mime_types - tuple containing mime type strings
		menu_item - menu item to be included in additional menu
		"""
		data = (mime_types, menu_item)
		self.popup_menu_actions.append(data)

	def register_viewer_extension(self, mime_types, ExtensionClass):
		"""Register viewer extension class for specified list of mime types"""
		data = (mime_types, ExtensionClass)
		self.viewer_extensions_classes.append(data)

	def plugin_class_exists(self, class_name):
		"""Check if specified class name exists in active plugins"""
		result = False

		for PluginClass in self.plugin_classes.values():
			if PluginClass.__name__ == class_name:
				result = True
				break

		return result

	def get_provider_by_path(self, path):
		"""Return provider class related to path"""
		protocol = 'file' if '://' not in path else path.split('://', 1)[0]
		return self.get_provider_by_protocol(protocol)

	def get_provider_by_protocol(self, protocol):
		"""Return provider class specified by protocol"""
		result = None

		if protocol in self.provider_classes.keys():
			result = self.provider_classes[protocol]

		return result

	def get_provider_for_archive(self, mime_type):
		"""Return provider class for specified archive mime type."""
		result = None

		if mime_type in self.archive_provider_classes:
			result = self.archive_provider_classes[mime_type]

		return result

	def get_column_extension_classes(self, BaseClass):
		"""Get column extension classes for specified list class"""
		result = []

		for ListClass, ExtensionClass in self.column_extension_classes:
			if issubclass(BaseClass, ListClass):
				result.append(ExtensionClass)

		return result

	def get_viewer_extension_classes(self, mime_type):
		"""Get list of extension classes for specified mime type"""
		result = []
		is_subset = self.associations_manager.is_mime_type_subset

		# get all classes that match any of the mime types defined
		for mime_types, ExtensionClass in self.viewer_extensions_classes:
			matched_types = [iter_mime_type for iter_mime_type in mime_types
							 if is_subset(mime_type, iter_mime_type)]

			if len(matched_types) > 0:
				result.append(ExtensionClass)

		return result

	def set_command_entry_text(self, text):
		"""Set command entry text and focus if specified."""
		self.command_edit.set_text(text)
		self.command_edit.set_position(len(text))
		self.show_command_entry()

	def append_text_to_command_entry(self, text):
		"""Append additional text to command entry."""
		current_text = self.command_edit.get_text()
		current_position = self.command_edit.get_position()

		if len(current_text) > 0:
			current_text += ' '

		current_text += text

		self.command_edit.set_text(current_text)
		self.command_edit.set_position(current_position)
		self.show_command_entry()

	def set_clipboard_text(self, text):
		"""Set text data to clipboard"""
		self.clipboard.set_text(text)

	def set_clipboard_item_list(self, operation, uri_list):
		"""Set clipboard to contain list of items

		operation - 'copy' or 'cut' string representing operation
		uri_list - list of URIs

		"""
		targets = [
				('x-special/gnome-copied-files', 0, 0),
				("text/uri-list", 0, 0)
			]
		raw_data = '{0}\n'.format(operation) + '\n'.join(uri_list)

		def get_func(clipboard, selection, info, data):
			"""Handle request from application"""
			target = selection.get_target()
			selection.set(target, 8, raw_data)

		def clear_func(clipboard, data):
			"""Clear function"""
			pass

		# set clipboard and return result
		return self.clipboard.set_with_data(targets, get_func, clear_func)

	def get_clipboard_text(self):
		"""Get text from clipboard"""
		return self.clipboard.wait_for_text()

	def get_clipboard_item_list(self):
		"""Get item list from clipboard"""
		result = None
		selection = self.clipboard.wait_for_contents('x-special/gnome-copied-files')

		# in case there is something to paste
		if selection is not None:
			data = selection.data.splitlines(False)

			operation = data[0]
			uri_list = data[1:]

			result = (operation, uri_list)

		return result

	def is_clipboard_text(self):
		"""Check if clipboard data is text"""
		return self.clipboard.wait_is_text_available()

	def is_clipboard_item_list(self):
		"""Check if clipboard data is URI list"""
		return self.clipboard.wait_is_uris_available()

	def is_archive_supported(self, mime_type):
		"""Check if specified archive mime type is supported."""
		return mime_type in self.archive_provider_classes

	def show_about_window(self, widget=None, data=None):
		"""Show about window"""
		window = AboutWindow(self)
		window.show()
		return True

	def show_advanced_rename(self, widget, data=None):
		"""Show advanced rename tool for active list"""
		if len(self.rename_extension_classes) > 0 \
		and issubclass(self._active_object.__class__, ItemList):
			active_object = self.get_active_object()

			if issubclass(active_object.__class__, ItemList):
				AdvancedRename(active_object, self)

		elif not issubclass(self._active_object.__class__, ItemList):
			# active object is not item list
			dialog = Gtk.MessageDialog(
								self,
								Gtk.DialogFlags.DESTROY_WITH_PARENT,
								Gtk.MessageType.INFO,
								Gtk.ButtonsType.OK,
								_(
									'Active object is not item list. Advanced '
									'rename tool needs files and directories.'
								)
							)
			dialog.run()
			dialog.destroy()

		elif len(self.rename_extension_classes) == 0:
			# no extensions found, report error to user
			dialog = Gtk.MessageDialog(
								self,
								Gtk.DialogFlags.DESTROY_WITH_PARENT,
								Gtk.MessageType.INFO,
								Gtk.ButtonsType.OK,
								_(
									'No rename extensions were found. Please '
									'enable basic rename options plugin and try '
									'again.'
								)
							)
			dialog.run()
			dialog.destroy()

			# show preferences window
			self.preferences_window._show(None, tab_name='plugins')

		return True

	def show_find_files(self, widget=None, data=None):
		"""Show find files tool"""
		if len(self.find_extension_classes) > 0:
			# create find files window
			FindFiles(self._active_object, self)

		else:
			# no extensions found, report error to user
			dialog = Gtk.MessageDialog(
								self,
								Gtk.DialogFlags.DESTROY_WITH_PARENT,
								Gtk.MessageType.INFO,
								Gtk.ButtonsType.OK,
								_(
									'No extensions for finding files were found. Please '
									'enable basic find file options plugin and try again.'
								)
							)
			dialog.run()
			dialog.destroy()

			# show preferences window
			self.preferences_window._show(None, tab_name='plugins')

		return True

	def show_keyring_manager(self, widget=None, data=None):
		"""Show keyring manager if available"""
		if self.keyring_manager.is_available():
			# create and show keyring manager
			try:
				KeyringManagerWindow(self)

			except InvalidKeyringError:
				# keyring is not available, let user know
				dialog = Gtk.MessageDialog(
									self,
									Gtk.DialogFlags.DESTROY_WITH_PARENT,
									Gtk.MessageType.INFO,
									Gtk.ButtonsType.OK,
									_('Keyring is empty!')
								)
				dialog.run()
				dialog.destroy()

		else:
			# keyring is not available, let user know
			dialog = Gtk.MessageDialog(
								self,
								Gtk.DialogFlags.DESTROY_WITH_PARENT,
								Gtk.MessageType.INFO,
								Gtk.ButtonsType.OK,
								_(
									'Keyring is not available. Make sure you have '
									'Python Gnome keyring module installed.'
								)
							)
			dialog.run()
			dialog.destroy()

		return True

	def check_for_new_version(self, widget=None, data=None):
		"""Check for new versions"""
		version = VersionCheck(self)
		version.check()

		return True

	def add_control_to_status_bar(self, control):
		"""Add new control to status bar"""
		self.status_bar.pack_start(control, False, False, 0)
