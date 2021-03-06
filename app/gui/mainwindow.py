# -*- coding: utf-8 -*-
from PyQt4.QtCore import QSettings, Qt
from PyQt4.QtGui import QMainWindow, QWidget, QMessageBox, QMenu, QIcon, QVBoxLayout

from app import logging
from app.config import Config
from app.client import ClientFactory
from app.database import Database
from app.hosts import Hosts
from app.gui import actions
from app.gui.hostconfig import HostConfigDialog
from app.gui.mainwindow_ui import Ui_MainWindow
from app.gui.mytabwidget import MyTabWidget
from app.gui.process import ProcessManager


class DockWidgetTitleBar(QWidget):
    """
    Add this time widget with just some spacing from layout
    """
    def __init__(self):
        super(DockWidgetTitleBar, self).__init__()
        lay = QVBoxLayout()
        self.setLayout(lay)


class MainWindow(QMainWindow):
    def __init__(self, config):
        super(MainWindow, self).__init__()
        self.config = Config(config)
        db = Database(self.config.getConnectionString())
        db.create()
        self.hosts = Hosts(db)

        # menu used for each host
        self.hostMenu = QMenu()
        self.hostMenu.addAction(QIcon(':/ico/edit.svg'), "Edit", self.editHost)
        self.hostMenu.addAction(QIcon(':/ico/remove.svg'), "Delete", self.deleteHost)
        actions.addActionWithScreenChose(self.hostMenu, self.connectFrameless,
                                         ':/ico/frameless.svg', "Connect frameless")

        # setup main window
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)

        # when top level changed, we changing dock title bar
        self.dockWidgetTileBar = DockWidgetTitleBar()
        self.ui.hostsDock.setTitleBarWidget(self.dockWidgetTileBar)
        self.ui.hostsDock.topLevelChanged.connect(self.dockLevelChanged)

        # set global menu
        self.globalMenu = QMenu()
        self.globalMenu.addAction(QIcon(':/ico/add.svg'), 'Add host', self.addHost)
        # disable menu indicator
        self.ui.menu.setStyleSheet("QPushButton::menu-indicator {image: none;}")
        self.positionMenu = QMenu("Dock position")
        self.positionMenu.addAction("Left", lambda: self.setDockPosition(Qt.LeftDockWidgetArea))
        self.positionMenu.addAction("Right", lambda: self.setDockPosition(Qt.RightDockWidgetArea))
        self.positionMenu.addAction("Float", self.setDockFloat)
        self.globalMenu.addMenu(self.positionMenu)
        self.globalMenu.addAction('Quit', self.close)
        self.ui.menu.setMenu(self.globalMenu)

        # set events on hosts list
        self.ui.hostsList.itemDoubleClicked.connect(self.slotConnectHost)
        self.ui.hostsList.itemClicked.connect(self.slotShowHost)
        self.ui.hostsList.customContextMenuRequested.connect(self.slotShowHostContextMenu)

        # set tab widget
        self.tabWidget = MyTabWidget()
        self.setCentralWidget(self.tabWidget)

        self.ui.filter.textChanged.connect(self.setHostList)
        self.setHostList()
        self.restoreSettings()

    def setDockPosition(self, dockWidgetArea):
        if self.ui.hostsDock.isFloating():
            self.ui.hostsDock.setFloating(False)
        self.addDockWidget(dockWidgetArea, self.ui.hostsDock)

    def setDockFloat(self):
        if self.ui.hostsDock.isFloating():
            return
        # default title bar must be set before is float because sometimes window make strange crash
        self.ui.hostsDock.setTitleBarWidget(None)
        self.ui.hostsDock.setFloating(True)

    def dockLevelChanged(self, isFloating):
        if isFloating:
            # changing title bar widget if is not none, probably true will be only once on start with saved float state
            if self.ui.hostsDock.titleBarWidget():
                self.ui.hostsDock.setTitleBarWidget(None)
        else:
            self.ui.hostsDock.setTitleBarWidget(self.dockWidgetTileBar)

    def showFramelessWidget(self):
        self.t.show()
        self.t.setGeometry(self.frameGeometry())

    def getCurrentHostListItemName(self):
        return self.ui.hostsList.currentItem().text()

    def findHostItemByName(self, name):
        result = self.ui.hostsList.findItems(name, Qt.MatchExactly)
        resultLen = len(result)
        if resultLen != 1:  # should be only one host
            logging.error("Host not found. Got %d results" % resultLen)
        return result[0]

    def slotShowHostContextMenu(self, pos):
        """ slot needed to show menu in proper position, or i'm doing something wrong
        """
        self.hostMenu.exec_(self.ui.hostsList.mapToGlobal(pos))

    def addHost(self):
        hostDialog = HostConfigDialog(self.hosts)
        resp = hostDialog.add()
        if resp["code"]:
            self.setHostList()
        hostName = resp.get("name")
        if hostName:
            hostItem = self.findHostItemByName(hostName)
            self.slotConnectHost(hostItem)

    def editHost(self):
        hostDialog = HostConfigDialog(self.hosts)
        resp = hostDialog.edit(self.getCurrentHostListItemName())
        if resp["code"]:
            self.setHostList()

    def deleteHost(self):
        self.hosts.delete(self.getCurrentHostListItemName())
        self.setHostList()

    def connectFrameless(self, screenIndex=None):
        self.connectHost(self.getCurrentHostListItemName(), frameless=True, screenIndex=screenIndex)

    # Fix to release keyboard from QX11EmbedContainer, when we leave widget through wm border
    def leaveEvent(self, event):
        keyG = QWidget.keyboardGrabber()
        if keyG is not None:
            keyG.releaseKeyboard()
        event.accept()  # needed?

    def setHostList(self):
        """ set hosts list in list view """
        self.ui.hostsList.clear()
        self.ui.hostsList.addItems(self.hosts.getFilteredHostsNames(self.ui.filter.text()))
    
    def slotShowHost(self, item):
        # on one click we activating tab and showing options
        self.tabWidget.activateTab(item)

    def slotConnectHost(self, item):
        self.connectHost(unicode(item.text()))

    def connectHost(self, hostId, frameless=False, screenIndex=None):
        hostId = unicode(hostId)  # sometimes hostId comes as QString
        tabPage = self.tabWidget.createTab(hostId)
        tabPage.reconnectionNeeded.connect(self.connectHost)

        if frameless:
            self.tabWidget.detachFrameless(tabPage, screenIndex)

        execCmd, opts = self.getCmd(tabPage, hostId)
        ProcessManager.start(hostId, tabPage, execCmd, opts)

    def getCmd(self, tabPage, hostName):
        host = self.hosts.get(hostName)

        # set tabPage widget
        width, height = tabPage.setSizeAndGetCurrent()
        # 1et widget winId to embed rdesktop
        winId = tabPage.x11.winId()

        # set remote desktop client, at this time works only with freerdp
        remoteClientType, remoteClientOptions = self.config.getRdpClient()
        remoteClient = ClientFactory(remoteClientType, **remoteClientOptions)
        remoteClient.setWindowParameters(winId, width, height)
        remoteClient.setUserAndPassword(host.user, host.password)
        remoteClient.setAddress(host.address)
        return remoteClient.getComposedCommand()

    def saveSettings(self):
        settings = QSettings("MyRDP")
        settings.setValue("geometry", self.saveGeometry())
        settings.setValue("windowState", self.saveState())

    def restoreSettings(self):
        settings = QSettings("MyRDP")
        try:
            self.restoreGeometry(settings.value("geometry").toByteArray())
            self.restoreState(settings.value("windowState").toByteArray())
        except Exception:
            logging.debug("No settings to restore")

    def closeEvent(self, event):
        if not ProcessManager.hasActiveProcess:
            self.saveSettings()
            return
               
        msgBox = QMessageBox(self, text="Are you soure do you want to quit?")
        msgBox.setWindowTitle("Exit confirmation")
        msgBox.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
        ret = msgBox.exec_()
        if ret == QMessageBox.Cancel:
            event.ignore()
            return
        
        self.saveSettings()
        ProcessManager.killemall()
        event.accept()
